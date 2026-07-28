# AGENTS.md

本文件记录 pr-learner 项目的长期约定，供 agent 和贡献者遵循。新会话请先读本文件与 `docs/handoff.md`。

## 项目定位

用 agent 阅读 GitHub PR，把读到的知识沉淀成分类 Markdown 知识库，并提供 Web/API 检索。**记录知识是 agent 的工作，用户只做 review**。

## 语言约定

- 对话、文档、项目知识默认用**简体中文**。
- 代码标识符、命令、路径、协议名等不宜翻译的内容保留原文。

## 架构约定

- 代码在 `src/pr_learner/`，模块职责单一、可组合，数据流单向：`discover → fetch → (LLM/agent 分析) → drafts → (用户 review) → store → query/api`。
- `fetch.py` 是唯一与 `gh` 交互处，subprocess 一律**列表传参**防注入。`search_prs` 的 argv 拼装每一条都对应实测过的 gh 静默失败，改动前先读函数内注释与 `docs/decisions/0004`。
- `llm.py` 是纯传输层，不认识业务；`system` 必填，不给默认值（默认值会静默串到别的业务提示词）。提示词模板一律外置在 `prompt_templates/<用途>/`，代码里不写死。
- `discover.py` 里模型只能重排真实候选：number 与 url 只信 gh 返回的；LLM 任一步失败都降级成未排序结果而非报错。
- `query.py` 纯内存检索，构建在 `store.load_all` 之上；数据量变大可换 SQLite/全文索引但**保持函数签名不变**。
- `api.py` 无内置鉴权，仅面向本地/内网；公网暴露需前置网关加访问控制。`GhError` → 400、`LlmError` → 502。
- 前端为单文件 `static/index.html`（Vue 3 CDN + marked + DOMPurify），无构建步骤。多个流式 tab 的日志/状态/`ref` 必须各自独立（都是 `v-show`，同时挂载）。LLM 凭证只在内存，**不落 localStorage**。

## 知识库约定

- 每条知识一个文件：`knowledge/<分类slug>/<标题slug>.md`，YAML frontmatter 含 `title/category/tags/source_pr/date`。
- `knowledge/index.md` 由 `pr-learner reindex` 自动生成，**不要手改**。
- 草稿存 `knowledge/.drafts/*.json`，与正式库隔离；`load_all` 只扫 `*.md`，跳过 `index.md` 与 `README.md`。
- 用户 review（可编辑）后 approve，草稿转正并删除。

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
