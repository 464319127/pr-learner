# 前端第三方依赖（vendor）

前端不走 CDN：`src/pr_learner/static/vendor/` 下的文件随包分发，由 `api.py` 挂在
`/static/vendor` 提供。理由：

- 这是内网工具（默认 LLM base_url 是内网域名），unpkg 本来就可能不通；
- 原来没有 SRI，CDN 被换包无人察觉；
- 「一部分本地一部分 CDN」会让「断网还能不能用」变成说不清的问题。

## 清单

| 文件 | npm 包与版本 | 字节 | SHA256 | 加载时机 |
|---|---|---|---|---|
| `vue.global.prod.js` | `vue@3.5.41` | 166,624 | `45c5186437878319a4b86339f475e8e2f0b27e1752f9e6387ebb15854425847f` | head 同步 |
| `marked.min.js` | `marked@12.0.2` | 35,479 | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` | head 同步 |
| `purify.min.js` | `dompurify@3.4.13` | 29,474 | `9ab3d44d73c3e3947f9ab72e0f0bc15c7f1931d60b365ba261fc85fe59013c56` | head 同步 |
| `highlight.min.js` | `@highlightjs/cdn-assets@11.11.2` | 128,053 | `62960a35954a685dbe12958092f661a185231e9f5f5c44dc3c1e237d9e087d5a` | head 同步 |
| `highlight-github-dark.min.css` | 同上 `/styles/github-dark.min.css` | 1,315 | `9f208d022102b1d0c7aebfecd8e42ca7997d5de636649d2b31ea63093d809019` | head `<link>` |
| `mermaid.min.js` | `mermaid@11.16.1` | 3,566,058 | `18327bef70d96fb505fe7287d9f6a7362ebf07ff6576ddfaffb1a06f3e1a2954` | **懒加载**（首次出现图表时才插 `<script>`） |

首屏合计约 353 KB；`mermaid.min.js` 占仓库体积的 95%，只在正文里真的有
`<pre class="mermaid">` 时才下载。

## 更新方式

```bash
V=src/pr_learner/static/vendor
curl -fsSLo $V/vue.global.prod.js  https://unpkg.com/vue@3.5.41/dist/vue.global.prod.js
curl -fsSLo $V/marked.min.js       https://unpkg.com/marked@12.0.2/marked.min.js
curl -fsSLo $V/purify.min.js       https://unpkg.com/dompurify@3.4.13/dist/purify.min.js
curl -fsSLo $V/highlight.min.js    https://unpkg.com/@highlightjs/cdn-assets@11.11.2/highlight.min.js
curl -fsSLo $V/highlight-github-dark.min.css \
    https://unpkg.com/@highlightjs/cdn-assets@11.11.2/styles/github-dark.min.css
curl -fsSLo $V/mermaid.min.js      https://unpkg.com/mermaid@11.16.1/dist/mermaid.min.js
shasum -a 256 $V/*   # 更新上表
```

`-f` 不可省：没有它 404 会产出一个内容是 HTML 错误页的「成功」文件。

## 几个已实测的约束

- **marked 锁 12.0.2**：原来写的是浮动 `marked@12`，今天解析出来就是 12.0.2，所以本次
  行为零变化。`latest` 已到 18.x，跨 6 个大版本的升级单独做。
- **highlight.js 用 common 打包版，不做按需**：`@highlightjs/cdn-assets` 顶层只有
  `highlight.js`(342 KB) 和 `highlight.min.js`(128 KB)，**没有 core-only bundle**；
  「core + 按需语言」必须跑 highlight.js 自己的构建工具链，等于给这个无构建步骤的项目
  引入构建步骤，为省 82 KB 不值。common 版已含 Bash/Python/YAML/Diff/C++/TypeScript/Makefile。
  以后缺 `cmake`/`llvm` 就往 vendor 丢一个 0.5~2.8 KB 的 `languages/*.min.js`。
- **highlight.js 没有 `cuda` 语言**，`languages/cuda.min.js` 是 404。CUDA 用 `language-cpp`，
  代码里另外 `hljs.registerAliases(['cuda','cu','cuh'], {languageName:'cpp'})` 兜住模型不听话。
- **mermaid 用 UMD 而不是 ESM**：`dist/mermaid.min.js` 末行是
  `globalThis["mermaid"] = …`，普通 `<script src>` 就能拿到全局 `mermaid`，不需要
  import map。ESM 入口首屏只有 30 KB，但要 vendor 135 个 chunk 才能离线可用。
- **不加 `GZipMiddleware`**：它会包住整个 app 包括两条 SSE，压缩流的缓冲会让
  「实时执行过程」卡住不刷新，那正是 `docs/decisions/0003` 的核心价值。`StaticFiles`
  自带 etag/304，够了。
- `pyproject.toml` 的 `artifacts` 必须是 `src/pr_learner/static/**/*`；写成 `static/*`
  时 vendor/ 不会进 wheel，本地测试全绿、只在 Docker 构建后炸。
- `.gitattributes` 把 `*.min.js` / `*.min.css` 标成 `-diff`，否则 `git diff` 会吐出
  3.5 MB 的一行。
