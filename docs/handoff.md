# docs/handoff.md

> 交接文档。新会话请先读 `AGENTS.md` 和本文件。最后更新：2026-08-12

## Goal

搭建 pr-learner：用 agent 阅读 GitHub PR，分类沉淀知识库，并提供 Web/API 供查找、提交分析与检索。

## Completed

- 项目骨架：`fetch/store/query/api/cli/drafts/analyze/llm/prompts/discover` + `pyproject.toml`（uv）+ `Dockerfile` + `docker-compose.yml`。
- 拉取 PR（`gh`，subprocess 列表传参防注入）→ Markdown。
- 分类文件知识库 + 自动 `index.md`；草稿制（`knowledge/.drafts/`）。正文最初是 Markdown，
  2026-08-12 起改成 HTML 片段（见下面那条与 decisions/0005）。
- FastAPI 服务：查询、分类统计、草稿 CRUD、approve 转正、`/api/analyze`（+ `/stream`）。
- **查找 PR**（2026-07-28）：`/api/discover/prs` 与 `/stream`、CLI `discover`、页面第 4 个 tab。
  LLM 两段介入（翻译查询 → gh 搜索 → 筛选排序写简介），结果带「分析此 PR」按钮接回分析流程。见 decisions/0004。
  状态筛选三档（已合并 / 未关闭 / 已关闭未合并，各自的 gh flag 组合见 `fetch._STATE_FLAGS`）；
  结果列表可按价值 / 创建时间 / 更新时间重排，排序在前端做、不重新请求 gh。
- LLM 传输层抽成 `llm.py`、模板读取抽成 `prompts.py`；提示词模板改子目录 `prompt_templates/{analyze,discover}/`。
- **知识正文改用 HTML 片段**（2026-08-12）：新增 `htmltext.py`（`strip_tags`/`detect_content_format`/`markdown_to_html`）；
  `store` 支持 `.md`/`.html` 双格式（**扩展名为唯一权威**）+ `rewrite_as`；`content_format` 全链路流转
  （模型 JSON → analyze → 草稿 → API → 页面单选 → 落盘扩展名，approve/create 都不重新嗅探）；
  检索对 HTML 先 `strip_tags`；新增 `pr-learner convert`（默认 dry-run）；`max_tokens` 4096 → 20000
  且可用 `PR_LEARNER_ANALYZE_MAX_TOKENS` 覆盖（原值 4096 现存最长的知识就已顶到，是既存 bug；
  20000 是按正文软上限 10000 字≈5600~8300 token 留 2~3 倍余量定的）。
  提示词新增 `prompt_templates/analyze/html_rules.md`（标签/class/语言白名单）。见 decisions/0005。
- **前端富渲染**（2026-08-12）：6 个依赖全部本地 vendor（`static/vendor/`，只 mount 这个子目录），
  mermaid 3.5MB 懒加载；`renderMd` → `renderBody` + 自定义指令 `v-rich`；DOMPurify 显式白名单
  （默认 profile 放行 `<style>` 与内联 `style=`，这是安全修复）；hljs 代码高亮 + mermaid
  `render()` + `Map<src,svg>` 缓存；`.markdown-body` → `.rich-body` 并新增卡片/分栏/指标/折叠样式；
  查询页折叠态改纯文本摘录、展开才富渲染；草稿页加格式单选与「已移除 N 处」提示。
- Vue 单页前端：查找 PR / 分析 PR / 查询知识 / 待审草稿四页；两个流式 tab 状态完全隔离；
  LLM 凭证前两页共享且只在内存。
- 测试 171 条，含 `test_llm.py`（httpx MockTransport 覆盖 SSE 解析与回退分支，补上了原先的空白）、
  `test_fetch_search.py`（每条断言对应一个实测过的 gh 陷阱）、`test_discover.py`（防幻觉与降级）、
  `test_frontend_sort.py` / `test_frontend_render.py`（抽出页面真实 JS 交给 node 跑）、
  `test_htmltext.py`（纯函数、零 IO）、`test_prompt_html_contract.py`（提示词与前端白名单逐项一致、
  承诺的 `language-XXX` 真在 hljs 包里）、`test_packaging.py`（`artifacts` 的 `**/*` 与 vendor 文件齐全）、
  `test_cli_convert.py`（dry-run 不写盘、`--apply` 保留原 `date`）。
- **存量知识已收敛成 HTML**（2026-08-12）：`pr-learner convert --apply` 转完 8 条，知识库现在
  只有 `.html`（`index.md` / `README.md` 除外）。frontmatter 的 `date` 全部保住（最早 07-24），
  转出的标签只有 `h2 h3 p ul ol li pre code strong table thead tbody tr th td`——**逐项在白名单内**。
  两点已知局限：源 md 里唯一的代码块 fence 没写语言，转出的 `<pre><code>` 不带 `language-XXX`
  故**不着色**；卡片/折叠/mermaid 一个都没有。两者都只能靠重新走一次「分析 PR」补。
- 项目知识文档：`AGENTS.md`、`docs/decisions/0001~0005`、`docs/vendor.md`、`TODO.md`、`prompt.md`。

## Decisions

- 知识存分类文件库（可 git、可读、简单）——见 decisions/0001。
- 记录采用草稿制，agent 生成 / 用户 review——见 decisions/0002。
- 页面提交 PR 由服务端调 LLM API 分析（Anthropic messages 兼容，默认 baidu-int oneapi）——见 decisions/0003。
- 查找 PR 让 LLM 两段介入，中间夹真实 gh 搜索；模型只能重排不能造事实；任一步失败都降级不报错——见 decisions/0004。
- 知识正文改用受限 HTML 片段，扩展名为格式权威，渲染依赖全本地 vendor——见 decisions/0005
  （取代 0001 里「正文用 Markdown」那一部分）。

## Open Problems

- 服务无鉴权，仅限本地/内网。本次新增了 `/static/vendor` 静态路由，**只挂 vendor 子目录**没扩大暴露面。
- 前端无框架级测试，但四处最容易静默失效的地方各有一条护栏：
  `test_stream_event_types_handled_by_frontend`（后端事件类型改名而前端没跟上）、
  `test_frontend_sends_all_required_analyze_fields`（请求体漏字段导致 422）、
  `tests/test_frontend_sort.py`（排序方向反了 / 就地 sort 打乱价值顺序）、
  `tests/test_frontend_render.py`（消毒库缺失时白屏 / class 白名单放行外壳样式）——
  都是从 index.html 抽出真实函数体交给 node 跑。其余交互仍需手动验证。
- **页面交互未在浏览器里验过**（本次无浏览器环境）：只做了机械验证——`node --check` 页面脚本、
  `html.parser` 查标签配对、在 node 里跑通 hljs 包与 `renderBody`。见下面的手动清单。
- 「查找 PR」的真实 LLM 路径只用本地 mock LLM 跑通（覆盖了成功、幻觉 number 被丢、
  连接失败双重降级）；真实模型的输出质量（翻译准不准、简介写得好不好）尚未用真实 key 评估过。
- GitHub search API 限流 30 次/分钟，高频使用会撞限额，目前只做了中文报错提示，没做本地限流。
- `.claude/settings.local.json` 已在 `.gitignore`（通用 `settings.json` 仍提交）、`prompt.md` 已跟踪、
  `.trees/`（git worktree 目录）本次加进 `.gitignore`——这条原先的「归属未定」已经不成立。

## Next Steps

1. 在浏览器里走一遍下面的手动清单（本次唯一没覆盖的一环）。
2. 用真实 key 在页面上跑几轮「查找 PR」，评估翻译与简介质量。
3. 再攒几条真实分析后回看 `html_rules.md`：目前 1 条样本零违规，样本量还太小。

## Verification

已跑过的：

- `uv run pytest -q` → 171 passed
- `uv run ruff check src tests` → 仍是那 8 条既有告警（cli.py 2×B008、drafts.py DTZ005、
  store.py DTZ011、test_api.py I001+3×PLR0402），本次没新增
- `uv run ruff format --check src tests` → 只剩 `api.py` / `fetch.py` 两处**既有**漂移
  （把 `git show HEAD:<file>` 喂给 ruff 逐条比对，确认与本次改动无关；自己引入的漂移已格式化）
- `node --check` 抽出的页面 `<script>`（533 行）→ 通过；`html.parser` 查 index.html 标签配对 → 无未闭合
- 在 node 里探过 vendor 的 hljs 包：36 种语言，`html_rules.md` 承诺的 16 个名字一个不缺，
  `registerAliases(['cuda','cu','cuh'])` 解析到 C++，`highlight()` 正常吐 `hljs-*` span
- `uv run pr-learner convert`（dry-run）→ 列出 8 条、`git status` 未变；随后 `--apply` 真转，
  8 条落 `.html`、`date` 未被改成今天、`index.md` 的 diff 恰好 8 增 8 删（只有扩展名变）、
  `pr-learner search --keyword tensor` 仍命中（`strip_tags` 检索的证据）
- **真实模型产出的第一条 HTML 逐项核对过**（用户在页面上跑并 approve 的「累积型误差…ReplaySSM」，
  10321 字，比 10000 软上限只超 3%）：**零白名单外标签与 class**；6 张表格全部包了 `table-wrap`；
  3 张卡片用了 tip/warn/note 三种语气；mermaid / 分栏 / 指标 / 结论各一处；`language-python`
  与 `language-diff` 都在 vendor 的 hljs 包里；3 个代码块**无漏转义的裸标签**。唯一没用到的是
  `<details>`（这条没有需要折叠的长内容）。提示词契约按预期生效，`html_rules.md` 暂不需要调。
- `max_tokens` 默认值 16384 → 20000（正文软上限同步 3500 → 10000 字，含标签）；
  `python -c "from pr_learner import analyze; print(analyze.DEFAULT_MAX_TOKENS)"` → 20000
- `uv build` + 解包 wheel → `prompt_templates/**` 与 `static/vendor/**` 都在包内
  （`artifacts` 写成 `/*` 时本地测试全绿但 wheel 里会缺文件，容器里 `StaticFiles` 构造即抛异常）

尚需人工在浏览器里确认（后端测试与 node 都覆盖不到）：

1. 四个 tab 逐个走一遍；「分析 PR」页填凭证 + PR 地址 → 确认**没有 422**
2. 两个流式 tab 来回切，确认日志与「进行中」状态不互相污染、自动滚动各滚自己那个框
3. 结果卡片「分析此 PR」→ 切 tab、URL 已填、自动开跑；结果列表切排序档，确认切回「价值」能复原
4. 草稿页：正文粘 `<pre class='mermaid'>graph TD; A-->B</pre>` 看出图；改成语法错的源码看到
   「源码 + 一行红字」而非红色大 SVG；连续打字观察图**不闪烁**（缓存命中的证据）；
   DevTools Network 确认 `mermaid.min.js` 只在首次出现图表时下载、且切到 review tab 才发起
5. 三条以上草稿分别切格式，确认互不串台（单选 `name` 带 draft id 的证据）；
   正文故意写不转义的 `vector<int>` → 应看到「⚠ 已移除 N 处…」
6. 查询页：搜一个**只出现在 HTML 正文里的关键词**（验 `strip_tags` 检索）→ 展开看富渲染；
   折叠态是三行纯文本摘录
7. 把 `static/vendor/purify.min.js` 临时改名 → 刷新应看到转义原文而**不是白屏**
8. `docker compose up --build` 后重跑第 1、6 步——这一步专门验 wheel 打包
