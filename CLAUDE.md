# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言

本仓库的对话回复与文档默认使用中文。代码标识符、命令保持原样。

## 项目目的

pr-learner 让 agent 阅读 GitHub PR，把读到的知识按分类沉淀成 Markdown 知识库，并提供 Web/API 检索。**agent 是阅读和总结的主体**，Python 只提供三类工具：拉取 PR、写入知识、检索知识。

## 核心工作流

0. `discover`（页面「查找 PR」）先解决「该读哪个 PR」：LLM 把关键词（可中文）翻成 GitHub 搜索语法，`gh search prs` 拿真实候选，LLM 再筛选排序并写中文简介。
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

uv run pr-learner discover "cache" --repo owner/repo --state merged   # 搜索相关 PR（不调 LLM）
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

- `fetch.py` — 唯一与 `gh` 交互的地方。`_run_gh` 以**列表**传参调用 subprocess（不拼接字符串，防注入），带 `timeout` 并把 gh 的英文报错/退出码翻成中文提示。`fetch_pr` 组合三次 gh 调用（`pr view --json`、`pr diff`、`api .../comments`）返回 `PullRequest` dataclass；`to_markdown` 把它渲染成 agent 友好、diff 超长自动截断的 Markdown。`search_prs` 封装 `gh search prs` 返回 `PrSummary` 列表——**改动前必读函数内注释**，argv 拼装顺序、`--` 分隔符、`shlex` 切 token、剥掉 `repo:` 限定符、`_STATE_FLAGS` 里三档状态各自的 flag 组合，每一条都对应一个实测过的 gh 静默失败（详见 `docs/decisions/0004`）。
- `llm.py` — LLM 传输层，**不认识任何业务**：`call` / `iter_stream`（兼容 Anthropic 与 OpenAI 两种响应及流式格式）、`parse_json_object`、`LlmError`。`system` 是**必填关键字参数**：默认值填别人的提示词会让模型返回结构不对但仍是合法 JSON 的结果，静默产出空列表且测试抓不到。
- `prompts.py` — 提示词模板读取。`read(name)` / `render(name, **values)`，name 是相对 `prompt_templates/` 的路径。`render` 用 `str.replace` 而非 `str.format`——模板里会填 JSON（满是 `{` `}`）。
- `store.py` — 知识持久层。`save_knowledge` 写带 frontmatter 的 Markdown 并触发 `rebuild_index`；`load_all` 扫描整个知识库返回结构化列表，是 query 层的数据源。`_slugify` 保留中文生成文件名。
- `query.py` — 纯内存检索，构建在 `store.load_all` 之上。关键词/分类/标签之间是 AND 关系。数据量变大时可换 SQLite/全文索引而**保持函数签名不变**。
- `analyze.py` — 「一个 PR → 一条知识草稿」。传输走 `llm.py`，模板走 `prompts.py`；模块顶部保留 `AnalyzeError` / `_extract_text` / `_parse_json_object` 等别名做向后兼容，`analyze_pr*` 内部**按模块全局名调用** `_call_llm` / `_iter_llm_stream`（测试靠 monkeypatch 这两个名字打桩，改成直呼 `llm.call` 会让替身失效）。
- `discover.py` — 「关键词 + 仓库 → 相关 PR 列表」。`build_query` 翻译查询、`rank_prs` 筛选排序写简介、`discover_prs_stream` 串成 SSE 事件。依赖 `llm` + `prompts` + `fetch`，**不依赖 `analyze`**。两处不可退让的约束：`join_ranked` 按 number 与真实候选 join，候选里没有的 number 一律丢弃、链接只用 gh 返回的 url（模型不能凭空造 PR）；模型任何一步失败都降级成未排序的搜索结果而非报错（LLM 抽风一次不该让用户什么都看不到）。
- `api.py` — FastAPI，薄封装 `query` / `analyze` / `discover`。知识库目录由环境变量 `PR_LEARNER_KNOWLEDGE_DIR` 覆盖（默认 `./knowledge`）。`GhError` → 400、`LlmError` → 502。**服务无内置鉴权**，仅面向本地/内网只读查询；公网暴露需前置网关加访问控制。
- `cli.py` — Typer 入口，把上述模块串成命令，是 agent 和人的统一操作面。`discover` 命令不调 LLM（agent 自己会写 GitHub 搜索语法）。
- `static/index.html` — Vue 3 CDN 单页，四个 tab：查找 PR / 分析 PR / 查询知识 / 待审草稿。LLM 凭证由前两个 tab 共享（`this.llm`），**只在内存里，不落 localStorage**。两个流式 tab 的日志、进行中状态、`ref` 与自动滚动目标各自独立——同名 `ref` 会互相覆盖，因为两个框都是 `v-show` 同时挂载。查找结果的排序（`sortedResults`）在前端做，不重新请求 gh；「价值」档必须原样返回后端顺序且**不能就地 sort**（会永久打乱模型排的顺序，切回来也回不去），日期缺失的条目两个方向都沉底。这段逻辑由 `tests/test_frontend_sort.py` 抽出真实函数体交给 node 执行来守。

数据流向单一：`discover → fetch → (agent 阅读) → store → query/api`。

### 提示词模板

提示词**不写死在代码里**，维护在 `prompt_templates/` 下的纯文本文件，用户和 agent 直接编辑即可：

```
prompt_templates/
  analyze/    system.md（{output_template} 占位）  user.md（{pr_markdown}）  output.json
  discover/   query_system.md  query_user.md（{keyword} {repo}）
              rank_system.md（{output_template}）  rank_user.md（{keyword} {candidates}）  rank_output.json
```

改 `output.json` / `rank_output.json` 的字段名时须同步对应模块里的 `obj.get(...)` 读取键。
新增子目录后注意 `pyproject.toml` 的 `artifacts` 用的是 `prompt_templates/**/*`——写成 `/*` 只匹配直接子项，本地 editable 安装照旧能读到、测试全绿，**只在 wheel / Docker 构建后炸**。

## 知识库约定

- 每条知识一个文件：`knowledge/<分类slug>/<标题slug>.md`，YAML frontmatter 含 `title / category / tags / source_pr / date`。
- `knowledge/index.md` 由 `reindex` 自动生成，**不要手改**。
- 知识库随 git 版本化，可挂进 docker 容器（`docker-compose.yml` 以只读方式挂载）。

## 部署

Dockerfile 基于 `uv` 官方镜像，`uv sync --no-dev` 装运行依赖，并 apt 安装 `gh`（容器内 `fetch` 拉 PR 需要）。知识库通过卷挂载而非打进镜像，本地更新即时生效。

容器内 `gh` 的 GitHub 凭证通过 `.env` 的 `GH_TOKEN` 注入（compose 自动加载）。**注意**：macOS 上 gh 的 token 存在系统钥匙串而非 `~/.config/gh`，仅只读挂载配置目录不足以让容器登录，必须走 `GH_TOKEN`。生成方式：`echo "GH_TOKEN=$(gh auth token)" > .env`。`.env` 已被 `.gitignore` 忽略，模板见 `.env.example`。
