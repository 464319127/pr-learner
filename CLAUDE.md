# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言

本仓库的对话回复与文档默认使用中文。代码标识符、命令保持原样。

## 项目目的

pr-learner 让 agent 阅读 GitHub PR，把读到的知识按分类沉淀成 Markdown 知识库，并提供 Web/API 检索。**agent 是阅读和总结的主体**，Python 只提供三类工具：拉取 PR、写入知识、检索知识。

## 核心工作流

1. `fetch` 用 `gh` CLI 拉取指定 PR 的描述、diff、讨论评论和行内评审评论，渲染成 Markdown 输出到 stdout。
2. agent（Ducc/Claude）阅读这段 Markdown，总结出可复用的知识。
3. `save` 把总结按分类写入 `knowledge/<分类>/<标题>.md`，并自动重建 `knowledge/index.md`。
4. `search` / 查询服务按关键词、分类、标签检索已有知识。

前提：本地已执行 `gh auth login`。

## 常用命令

```bash
uv sync                                      # 创建虚拟环境并安装依赖
uv run pytest -q                             # 跑全部测试
uv run pytest tests/test_store_query.py::test_save_and_search   # 跑单个测试
uv run ruff check .                          # lint
uv run ruff format .                         # 格式化

uv run pr-learner fetch owner/repo 123       # 拉取 PR，输出 Markdown 供阅读
uv run pr-learner save "标题" 分类 内容.md --tags a,b --source-pr owner/repo#123
uv run pr-learner save "标题" 分类 - <<< "正文"   # 正文从 stdin 读
uv run pr-learner search --keyword 重试       # 检索
uv run pr-learner categories                 # 分类统计
uv run pr-learner reindex                    # 重建 index.md

uv run uvicorn pr_learner.api:app --port 8000   # 本地起查询服务
docker compose up --build                       # 容器化查询服务
```

## 架构

代码在 `src/pr_learner/`，模块职责单一、可组合：

- `fetch.py` — 唯一与 `gh` 交互的地方。`_run_gh` 以**列表**传参调用 subprocess（不拼接字符串，防注入）。`fetch_pr` 组合三次 gh 调用（`pr view --json`、`pr diff`、`api .../comments`）返回 `PullRequest` dataclass；`to_markdown` 把它渲染成 agent 友好、diff 超长自动截断的 Markdown。
- `store.py` — 知识持久层。`save_knowledge` 写带 frontmatter 的 Markdown 并触发 `rebuild_index`；`load_all` 扫描整个知识库返回结构化列表，是 query 层的数据源。`_slugify` 保留中文生成文件名。
- `query.py` — 纯内存检索，构建在 `store.load_all` 之上。关键词/分类/标签之间是 AND 关系。数据量变大时可换 SQLite/全文索引而**保持函数签名不变**。
- `analyze.py` — 调 LLM 把 PR 分析成知识草稿。**提示词不写死在代码里**，维护在 `prompt_templates/` 目录的纯文本文件：`system_prompt.md`（用 `{output_template}` 占位）、`user_prompt.md`（用 `{pr_markdown}` 占位）、`output_template.json`（期望的 JSON 输出结构）。`build_system_prompt` / `build_user_prompt` 负责读取并填充占位符。用户和 agent 直接编辑这些文件即可调整提示词，无需改代码。改 `output_template.json` 的字段名时须同步 `analyze_pr` 里的 `obj.get(...)` 读取键。
- `api.py` — FastAPI，薄封装 `query`。知识库目录由环境变量 `PR_LEARNER_KNOWLEDGE_DIR` 覆盖（默认 `./knowledge`）。**服务无内置鉴权**，仅面向本地/内网只读查询；公网暴露需前置网关加访问控制。
- `cli.py` — Typer 入口，把上述模块串成命令，是 agent 和人的统一操作面。

数据流向单一：`fetch → (agent 阅读) → store → query/api`。

## 知识库约定

- 每条知识一个文件：`knowledge/<分类slug>/<标题slug>.md`，YAML frontmatter 含 `title / category / tags / source_pr / date`。
- `knowledge/index.md` 由 `reindex` 自动生成，**不要手改**。
- 知识库随 git 版本化，可挂进 docker 容器（`docker-compose.yml` 以只读方式挂载）。

## 部署

Dockerfile 基于 `uv` 官方镜像，`uv sync --no-dev` 装运行依赖，只跑查询服务。知识库通过卷挂载而非打进镜像，本地更新即时生效。
