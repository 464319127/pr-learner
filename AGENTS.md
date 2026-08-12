# AGENTS.md

本文件记录 pr-learner 项目的长期约定，供 agent 和贡献者遵循。新会话请先读本文件与 `docs/handoff.md`。

## 项目定位

用 agent 阅读 GitHub PR，把读到的知识沉淀成分类文件知识库（正文默认受限 HTML 片段，Markdown 仍支持），并提供 Web/API 检索。**记录知识是 agent 的工作，用户只做 review**。

## 语言约定

- 对话、文档、项目知识默认用**简体中文**。
- 代码标识符、命令、路径、协议名等不宜翻译的内容保留原文。

## 架构约定

- 代码在 `src/pr_learner/`，模块职责单一、可组合，数据流单向：`discover → fetch → (LLM/agent 分析) → drafts → (用户 review) → store → query/api`。
- `fetch.py` 是唯一与 `gh` 交互处，subprocess 一律**列表传参**防注入。`search_prs` 的 argv 拼装每一条都对应实测过的 gh 静默失败，改动前先读函数内注释与 `docs/decisions/0004`。
- `llm.py` 是纯传输层，不认识业务；`system` 必填，不给默认值（默认值会静默串到别的业务提示词）。提示词模板一律外置在 `prompt_templates/<用途>/`，代码里不写死。
- `discover.py` 里模型只能重排真实候选：number 与 url 只信 gh 返回的；LLM 任一步失败都降级成未排序结果而非报错。
- `query.py` 纯内存检索，构建在 `store.load_all` 之上；数据量变大可换 SQLite/全文索引但**保持函数签名不变**。HTML 正文先 `htmltext.strip_tags` 再匹配，纯文本副本**不写回 `load_all` 的 dict**（那个 dict 直接当 `/api/search` 的 payload 返回，加一份等于翻倍）。
- `htmltext.py` 是正文格式的检测/纯文本化/转换的唯一去处：持久层不该长出 HTML parser，检索层不该内联 parser。`strip_tags` 用 `html.parser` 不用正则（实体解码、属性里的 `>`、畸形输出都靠状态机兜）；`detect_content_format` 只在模型没给格式时兜底，**宁可判 markdown 也不误判 html**。
- `api.py` 无内置鉴权，仅面向本地/内网；公网暴露需前置网关加访问控制。`GhError` → 400、`LlmError` → 502。静态资源**只 mount `/static/vendor` 子目录**，不把整个 `static/`（源码目录）暴露成公开 URL。
- 前端为单文件 `static/index.html`（Vue 3 + marked + DOMPurify + highlight.js + mermaid，**全部本地 vendor 在 `static/vendor/`**，版本与 SHA256 见 `docs/vendor.md`），无构建步骤。多个流式 tab 的日志/状态/`ref` 必须各自独立（都是 `v-show`，同时挂载）。LLM 凭证只在内存，**不落 localStorage**。
- 正文富渲染的三条硬约束：DOMPurify **必须显式配白名单**（默认 profile 放行 `<style>` 与内联 `style=`）；渲染入口用自定义指令 `v-rich` 而非 `v-html`（mermaid 换进去的 SVG 会被 Vue 重新 patch 抹掉）；mermaid 用 `render()` + `Map<src,svg>` 缓存而非 `run()`（后者的 `data-processed` 标记随 innerHTML 重建消失 → 必然重复渲染）。前端的标签/class 白名单与 `prompt_templates/analyze/html_rules.md` **必须逐项一致**，由 `tests/test_prompt_html_contract.py` 钉住。

## 知识库约定

- 每条知识一个文件：`knowledge/<分类slug>/<标题slug>.html`（分析产出的默认格式）或 `.md`（历史与手写），YAML frontmatter 含 `title/category/tags/source_pr/date/content_format`。
- **格式以扩展名为唯一权威**，frontmatter 的 `content_format` 只作自解释与兜底；未知值一律回落 `markdown`，永不抛异常。历史 `.md` 用 `pr-learner convert` 批量转（默认 dry-run，`--apply` 才写盘，走 `store.rewrite_as` 保住原 `date`）。
- `knowledge/index.md` 由 `pr-learner reindex` 自动生成，**不要手改**；它本身保持 Markdown（给 GitHub / 编辑器看的仓库门面）。
- 草稿存 `knowledge/.drafts/*.json`，与正式库隔离；`load_all` 只认 `.md`/`.html`，跳过 `index`/`README` 与**任何以 `.` 开头的路径段**（后者是显式不变量，不是靠 glob 碰巧躲开）。
- 用户 review（可编辑，含 HTML/Markdown 格式单选）后 approve，草稿转正并删除。

## 工作流约定（agent 自我要求）

- 长期约定写入本文件 `AGENTS.md`。
- 重要技术决策写入 `docs/decisions/`（背景/决定/理由/影响）。
- 每完成一个里程碑更新 `docs/handoff.md`。
- 明确的后续任务写入 `TODO.md`。
- 不保存完整聊天记录、猜测、过时结论或敏感信息。
- 关键保存时机：作出关键决定后、解决复杂问题后、完成里程碑后、结束会话前。
- 交互记录：消息带 `#log` / `#记录` 标记时由 hook 自动追加到 `prompt.md`。

## 开发命令

```bash
uv sync                                   # 安装依赖
uv run --group dev pytest -q              # 跑测试
uv run ruff check .                        # lint
uv run uvicorn pr_learner.api:app --port 8000   # 起服务
docker compose up --build                  # 容器化
```
