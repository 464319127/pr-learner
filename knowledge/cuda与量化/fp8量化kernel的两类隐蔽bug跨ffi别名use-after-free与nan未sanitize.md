---
category: CUDA与量化
date: '2026-07-24'
source_pr: sgl-project/sglang#32188
tags:
- fp8
- cuda-graph
- use-after-free
- deepgemm
- sglang
- 量化
title: fp8量化kernel的两类隐蔽bug：跨FFI别名use-after-free与NaN未sanitize
---

sgl-project/sglang PR #32188 在 H100 `deepep-4-gpu-h100` CI（TestTBOWithTPAttn）挂掉后，一次性根因修复了两个**独立**的 bug。二者都由 #30924 引入的"统一 JIT 量化 kernel"触发，且都只在特定条件下暴露，是排查 fp8 量化 + CUDA graph 问题的极佳案例。

## Bug 1：activation scale 的 use-after-free（报错 "pointer resides on host memory"）

- **机理**：sgl-deep-gemm(<=0.1.4.post1) 的 `get_mn_major_tma_aligned_tensor`，在输入"已经是 MN-major/TMA-aligned"的快路径下，跨 TVM-FFI 边界返回一个**非拥有的 `torch::from_blob` 别名**（指向入参存储），而不是拷贝。
- **触发**：调用方惯用写法 `down_input_scale = get_mn_major_tma_aligned_tensor(down_input_scale)` 把唯一引用覆盖掉 → 原存储被释放回 caching allocator 并被复用 → 但后续 enqueue 的 down GEMM 仍通过别名读这块 scale → use-after-free。
- **为何 #30924 才触发**：旧的 row-major scale 总走"拥有式拷贝"路径；统一 kernel 输出的 scale 本就 MN-major 对齐，第一次命中了 no-op 别名路径。
- **为何 TBO 下必现**：two-batch-overlap 让另一个 micro-batch 的层（几十个小分配）恰好插在 rebind 与 GEMM 之间，复用几乎必然发生。Blackwell 不受影响（`DEEPGEMM_NEED_TMA_ALIGNED_SCALES=False`）。
- **修复**：在 sglang wrapper 里，当 deep_gemm 返回的是入参别名时（`out.data_ptr() == sf.data_ptr()`）直接返回原 tensor，保住所有权。根治则要改 DeepGEMM 自身的所有权语义。

## Bug 2：统一 kernel 丢失非有限值 sanitization（capture 阶段 NaN）

- **机理**：被替换掉的每个旧 kernel（AOT v1 / JIT v2 / Triton）在转 fp8 前都用 IEEE `fminf/fmaxf` clamp。IEEE min/max **返回非-NaN 操作数**，所以 NaN/Inf 输入被量化成 ±448。新 kernel 只依赖 SATFINITE，而 SATFINITE **只饱和 inf/越界值，NaN 会被转成 fp8 NaN 码**。（另外 `amax=inf` → `__frcp_rn(inf)=0` → `inf*0=NaN`。）
- **为何需要 CI runner 条件才复现**：CUDA graph capture 的 warmup 在复用的、未初始化的 buffer 上跑模型；CI runner 因连续跑 job 显存是脏的、含 NaN 位模式，旧 kernel 会静默 sanitize，新 kernel 把 fp8 NaN 码传进下游 GEMM → `NaN detected! sampler: next_token_logits`。全新 GPU 发的是清零页，故本地难复现。
- **修复**：在 `per_token_group_quant.cuh` 的 fp32-scale 和 UE8M0 两条路径都恢复 clamp（UE8M0 用 `__hmin2/__hmax2`）。对有限输入与裸 SATFINITE **逐位一致**（UE8M0 的 2 的幂 scale 已把有限值限制在 ±448）。

## 排查方法论（值得学习的部分）

1. **一致性矩阵**：用一张 (配置 × masked路径 × flat路径 × 结果) 表格，证明前一个尝试 #32051（只 revert masked 路径）为何无效——NaN 藏在它没覆盖的 flat 路径（每个 block-fp8 linear 输入量化仍走新 kernel）。这解释了 `/rerun-test` 为何 5 次都同样 NaN 失败。
2. **字节级复现**：用 `PYTORCH_NO_CUDA_MEMORY_CACHING=1`（free 变成真正的 `cudaFree`）让 bug 1 的未修复路径逐字节复现 CI 报错。
3. **定量证据**：bug 1 生产路径 repro——未修复 688 个 NaN 行，修复后 0；bug 2 用 NaN/Inf 毒化输入探针对比。单测 105/105（92 原有 + 12 新 sanitization + 1 所有权回归）。

## 可迁移的教训

- 跨 FFI/语言边界返回的 tensor 要警惕**非拥有别名**；`x = f(x)` 这种自赋值会悄悄释放存储，在有 caching allocator + 异步 enqueue（CUDA graph / stream）时变成 use-after-free。
- fp8/低精度量化 kernel 必须显式处理**非有限值**；不能假设硬件转换指令（SATFINITE）会把 NaN 也饱和掉——它不会。
- CUDA graph capture warmup 跑在**脏显存**上，任何依赖"输入必然有限"的 kernel 都可能在 capture 阶段而非推理阶段炸。