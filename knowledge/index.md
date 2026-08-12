# 知识库索引

共 9 条知识。

## CUDA 与量化

- [用图级替换接入非协作 CUTLASS FP8 GEMM，消除 nvjet 的同步 memset 气泡](cuda-与量化/用图级替换接入非协作-cutlass-fp8-gemm消除-nvjet-的同步-memset-气泡.html) — 来源 sgl-project/sglang#22392

## CUDA 内核融合与推理性能

- [通过单次 CUDA launch 并发执行 MoE 路由、量化和索引打包，降低小批量解码的启动开销](cuda-内核融合与推理性能/通过单次-cuda-launch-并发执行-moe-路由量化和索引打包降低小批量解码的启动开销.html) — 来源 sgl-project/sglang#32699

## CUDA与量化

- [fp8量化kernel的两类隐蔽bug：跨FFI别名use-after-free与NaN未sanitize](cuda与量化/fp8量化kernel的两类隐蔽bug跨ffi别名use-after-free与nan未sanitize.html) — 来源 sgl-project/sglang#32188
- [用双 BF16 残差分解在 Tensor Core 上逼近 FP32 GEMM：拿 2 倍算力换接近 FP32 的精度](cuda与量化/用双-bf16-残差分解在-tensor-core-上逼近-fp32-gemm拿-2-倍算力换接近-fp32-的精度.html) — 来源 Tencent/hpc-ops#47

## CUDA与量化 / 推理内核优化

- [用环形缓冲把线性注意力 decode 的状态写回改成每 L 步一次：省一半 HBM 流量的前提是 tl.dot 必须跑在 Tensor Core，且 checkpoint 与缓存键长度必须严格对齐](cuda与量化-推理内核优化/用环形缓冲把线性注意力-decode-的状态写回改成每-l-步一次省一半-hbm-流量的前提是-tldot-必须跑在-tensor-core且-checkpoint-与缓存键长度必须严格对齐.html) — 来源 sgl-project/sglang#28451

## CUDA与量化 / 算子融合

- [在 eager 模式下用「位精确」Triton 融合替换 elementwise 小算子链：靠复现 aten 的每一次 bf16 舍入边界，换来 5% 端到端提速且输出 md5 不变](cuda与量化-算子融合/在-eager-模式下用位精确triton-融合替换-elementwise-小算子链靠复现-aten-的每一次-bf16-舍入边界换来-5-端到端提速且输出-md5-不变.html) — 来源 sgl-project/sglang#34314

## CUDA内核与MoE性能

- [SM90 上融合 MegaMoE 不一定带来收益，硬件能力、量化布局与调度必须共同匹配](cuda内核与moe性能/sm90-上融合-megamoe-不一定带来收益硬件能力量化布局与调度必须共同匹配.html) — 来源 deepseek-ai/DeepGEMM#323

## 推测解码与线性注意力

- [累积型误差不能走近似路径：ReplaySSM 用「一次性输出走 tf32、状态走按位复放」拆掉长文本退化](推测解码与线性注意力/累积型误差不能走近似路径replayssm-用一次性输出走-tf32状态走按位复放拆掉长文本退化.html) — 来源 sgl-project/sglang#28695

## 推测解码与采样

- [推测解码必须用真实草稿分布配合拒绝采样，才能同时保持目标分布正确性并提高接受长度](推测解码与采样/推测解码必须用真实草稿分布配合拒绝采样才能同时保持目标分布正确性并提高接受长度.html) — 来源 sgl-project/sglang#26312
