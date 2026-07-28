# docs/handoff.md

> 交接文档。新会话请先读 `AGENTS.md` 和本文件。最后更新：2026-07-28

## Goal

搭建 pr-learner：用 agent 阅读 GitHub PR，分类沉淀知识库，并提供 Web/API 供查找、提交分析与检索。

## Completed

- 项目骨架：`fetch/store/query/api/cli/drafts/analyze/llm/prompts/discover` + `pyproject.toml`（uv）+ `Dockerfile` + `docker-compose.yml`。
- 拉取 PR（`gh`，subprocess 列表传参防注入）→ Markdown。
- 分类 Markdown 知识库 + 自动 `index.md`；草稿制（`knowledge/.drafts/`）。
- FastAPI 服务：查询、分类统计、草稿 CRUD、approve 转正、`/api/analyze`（+ `/stream`）。
- **查找 PR**（2026-07-28）：`/api/discover/prs` 与 `/stream`、CLI `discover`、页面第 4 个 tab。
  LLM 两段介入（翻译查询 → gh 搜索 → 筛选排序写简介），结果带「分析此 PR」按钮接回分析流程。见 decisions/0004。
  状态筛选三档（已合并 / 未关闭 / 已关闭未合并，各自的 gh flag 组合见 `fetch._STATE_FLAGS`）；
  结果列表可按价值 / 创建时间 / 更新时间重排，排序在前端做、不重新请求 gh。
- LLM 传输层抽成 `llm.py`、模板读取抽成 `prompts.py`；提示词模板改子目录 `prompt_templates/{analyze,discover}/`。
- Vue 单页前端：查找 PR / 分析 PR / 查询知识 / 待审草稿四页；两个流式 tab 状态完全隔离；
  LLM 凭证前两页共享且只在内存。
- 测试 119 条，含 `test_llm.py`（httpx MockTransport 覆盖 SSE 解析与回退分支，补上了原先的空白）、
  `test_fetch_search.py`（每条断言对应一个实测过的 gh 陷阱）、`test_discover.py`（防幻觉与降级）、
  `test_frontend_sort.py`（抽出页面真实 JS 交给 node 跑）。
- 项目知识文档：`AGENTS.md`、`docs/decisions/0001~0004`、`TODO.md`、`prompt.md`。

## Decisions

- 知识存分类 Markdown（可 git、可读、简单）——见 decisions/0001。
- 记录采用草稿制，agent 生成 / 用户 review——见 decisions/0002。
- 页面提交 PR 由服务端调 LLM API 分析（Anthropic messages 兼容，默认 baidu-int oneapi）——见 decisions/0003。
- 查找 PR 让 LLM 两段介入，中间夹真实 gh 搜索；模型只能重排不能造事实；任一步失败都降级不报错——见 decisions/0004。

## Open Problems

- 服务无鉴权，仅限本地/内网。
- 前端无框架级测试，但三处最容易静默失效的地方各有一条护栏：
  `test_stream_event_types_handled_by_frontend`（后端事件类型改名而前端没跟上）、
  `test_frontend_sends_all_required_analyze_fields`（请求体漏字段导致 422）、
  `tests/test_frontend_sort.py`（排序方向反了 / 就地 sort 打乱价值顺序——从 index.html
  抽出真实函数体交给 node 跑，已验证两类回归都能抓到）。其余交互仍需手动验证。
- 「查找 PR」的真实 LLM 路径只用本地 mock LLM 跑通（覆盖了成功、幻觉 number 被丢、
  连接失败双重降级）；真实模型的输出质量（翻译准不准、简介写得好不好）尚未用真实 key 评估过。
- GitHub search API 限流 30 次/分钟，高频使用会撞限额，目前只做了中文报错提示，没做本地限流。
- `.claude/settings.local.json` 与 `prompt.md` 是否纳入/忽略 git 尚未定。

## Next Steps

1. 用真实 key 在页面上跑几轮「查找 PR」，评估翻译与简介质量，按需调
   `prompt_templates/discover/*.md`（改模板不用改代码）。
2. 决定 `prompt.md`、`.claude/settings.local.json` 的 git 归属（提交 or gitignore）。
3. 视需要给 search 加本地限流或结果缓存。

## Verification

- `uv run pytest -q` → 119 passed
- `uv run ruff check .` → 仅剩 8 条既有告警（B008/DTZ/PLR0402，本次改动前就有）
- `uv build` + 解包 wheel → `prompt_templates/analyze/` 与 `discover/` 都在包内
  （模板改子目录后必查：`artifacts` 写成 `/*` 时本地测试全绿但 wheel 里会缺文件）
- 真实 gh + mock LLM 端到端：查找非流式与 SSE 流式均正常，幻觉 number 被丢弃，
  LLM 不可用时两级降级（翻译失败 → 按原文搜 → 无命中则列最近 PR → 筛选失败则展示原始结果）
- `POST /api/analyze/stream` 用页面组装的请求体 → 200；缺凭证 → 422（凭证共享重构的回归点）
- 手动清单（后端测试覆盖不到）：
  1. 「分析 PR」页填凭证 + PR 地址 → 开始分析，确认**没有 422**
  2. 两个流式 tab 来回切，确认日志与「进行中」状态不互相污染、自动滚动各滚自己那个框
  3. 结果卡片「分析此 PR」→ 切 tab、URL 已填、自动开跑
  4. 结果列表切换排序档与升降序，确认切回「价值」档能恢复模型排的顺序
