# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言

本仓库的对话回复与文档默认使用中文。代码标识符、命令保持原样。

## 项目目的

pr-learner 让 agent 阅读 GitHub PR，把读到的知识按分类沉淀成文件知识库（正文默认 HTML 片段，Markdown 仍支持），并提供 Web/API 检索。**agent 是阅读和总结的主体**，Python 只提供三类工具：拉取 PR、写入知识、检索知识。

## 核心工作流

0. `discover`（页面「查找 PR」）先解决「该读哪个 PR」：LLM 把关键词（可中文）翻成 GitHub 搜索语法，`gh search prs` 拿真实候选，LLM 再筛选排序并写中文简介。
1. `fetch` 用 `gh` CLI 拉取指定 PR 的描述、diff、讨论评论和行内评审评论，渲染成 Markdown 输出到 stdout。
2. agent（Ducc/Claude）阅读这段 Markdown，总结出可复用的知识。
3. `save` 把总结按分类写入 `knowledge/<分类>/<标题>.{html,md}`，并自动重建 `knowledge/index.md`。
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
uv run pr-learner save "标题" 分类 正文.html --content-format html   # 正文按 HTML 存（落 .html）
uv run pr-learner search --keyword 重试       # 检索
uv run pr-learner categories                 # 分类统计
uv run pr-learner reindex                    # 重建 index.md
uv run pr-learner convert                    # 历史 .md 批量转 HTML：**默认 dry-run 只打印清单**
uv run pr-learner convert --apply            # 真正写盘（会删除原 .md，转换前先提交或 stash）

uv run uvicorn pr_learner.api:app --port 8000   # 本地起查询服务
docker compose up --build                       # 容器化查询服务
```

## 架构

代码在 `src/pr_learner/`，模块职责单一、可组合：

- `fetch.py` — 唯一与 `gh` 交互的地方。`_run_gh` 以**列表**传参调用 subprocess（不拼接字符串，防注入），带 `timeout` 并把 gh 的英文报错/退出码翻成中文提示。`fetch_pr` 组合三次 gh 调用（`pr view --json`、`pr diff`、`api .../comments`）返回 `PullRequest` dataclass；`to_markdown` 把它渲染成 agent 友好、diff 超长自动截断的 Markdown。`search_prs` 封装 `gh search prs` 返回 `PrSummary` 列表——**改动前必读函数内注释**，argv 拼装顺序、`--` 分隔符、`shlex` 切 token、剥掉 `repo:` 限定符、`_STATE_FLAGS` 里三档状态各自的 flag 组合，每一条都对应一个实测过的 gh 静默失败（详见 `docs/decisions/0004`）。
- `llm.py` — LLM 传输层，**不认识任何业务**：`call` / `iter_stream`（兼容 Anthropic 与 OpenAI 两种响应及流式格式）、`parse_json_object`、`LlmError`。`system` 是**必填关键字参数**：默认值填别人的提示词会让模型返回结构不对但仍是合法 JSON 的结果，静默产出空列表且测试抓不到。
- `prompts.py` — 提示词模板读取。`read(name)` / `render(name, **values)`，name 是相对 `prompt_templates/` 的路径。`render` 用 `str.replace` 而非 `str.format`——模板里会填 JSON（满是 `{` `}`）。
- `htmltext.py` — 正文格式的检测、纯文本化与转换，被 store / query / analyze / cli 共用（**不塞进 `store.py`**：持久层不该长出一个 HTML parser）。`strip_tags` 用 `html.parser` 而非正则——`convert_charrefs` 默认解掉 `&amp;/&lt;/&#39;/&nbsp;`，属性里和注释里的 `>` 骗不到状态机，模型的半截输出也不抛异常；行内标签边界插空串、块级插换行，这条**直接决定检索精度**（`<strong>内存</strong>泄漏` 要能被「内存泄漏」搜到）。`detect_content_format` 只在模型没给格式时兜底，**宁可判成 markdown 也不误判成 html**。`markdown_to_html` 给 `convert` 命令用，fence 渲染器产出的 `<pre><code class="language-cpp">` 正好对上前端 hljs 的契约。
- `store.py` — 知识持久层。`save_knowledge` 写带 frontmatter 的正文并触发 `rebuild_index`，`content_format` 是 keyword-only 且默认 markdown（老调用点零改动）；`load_all` 扫描整个知识库返回结构化列表（含 `content_format`，**由扩展名判定**），是 query 层的数据源；两个 glob 的结果**合并后再 sorted**（分别 sorted 会变成「先全部 md 再全部 html」），跳过 `index/README` 与**任何以 `.` 开头的路径段**（这让「天然忽略 `.drafts/`」从巧合变成显式不变量）。`rewrite_as` 原样搬运 frontmatter 只换正文与扩展名，给 `convert` 保住历史 `date`。`_slugify` 保留中文生成文件名。
- `query.py` — 纯内存检索，构建在 `store.load_all` 之上。关键词/分类/标签之间是 AND 关系；HTML 正文先 `htmltext.strip_tags` 再匹配，**纯文本副本不写回 `load_all` 的 dict**（正文 4~7KB，`/api/search` 直接返回该 dict，加一份等于 payload 翻倍）。数据量变大时可换 SQLite/全文索引而**保持函数签名不变**。
- `analyze.py` — 「一个 PR → 一条知识草稿」。传输走 `llm.py`，模板走 `prompts.py`；模块顶部保留 `AnalyzeError` / `_extract_text` / `_parse_json_object` 等别名做向后兼容，`analyze_pr*` 内部**按模块全局名调用** `_call_llm` / `_iter_llm_stream`（测试靠 monkeypatch 这两个名字打桩，改成直呼 `llm.call` 会让替身失效）。`DEFAULT_MAX_TOKENS` 默认 20000、可用 `PR_LEARNER_ANALYZE_MAX_TOKENS` 覆盖（它管的是**整个 JSON 输出**而不只是正文，正文软上限 10000 字≈5600~8300 token，加其余字段约 6000~9000，20000 留 2~3 倍余量；这个数只是上限不是预算，调大不多花钱，调小则把「写超一点」变成 JSON 截断报错。反向代价是部分 OpenAI 兼容模型输出上限就是 16384/8192，超过会 400，遇到用环境变量下调；`llm.py` 的默认值不动——那是传输层）；截断时 JSON 解析失败会报「疑似被 max_tokens 截断」而不是一句费解的格式错误。`_fields_from_obj` 处理 `content_format`：模型给了就 normalize，没给才走 `htmltext.detect_content_format`。
- `discover.py` — 「关键词 + 仓库 → 相关 PR 列表」。`build_query` 翻译查询、`rank_prs` 筛选排序写简介、`discover_prs_stream` 串成 SSE 事件。依赖 `llm` + `prompts` + `fetch`，**不依赖 `analyze`**。两处不可退让的约束：`join_ranked` 按 number 与真实候选 join，候选里没有的 number 一律丢弃、链接只用 gh 返回的 url（模型不能凭空造 PR）；模型任何一步失败都降级成未排序的搜索结果而非报错（LLM 抽风一次不该让用户什么都看不到）。
- `api.py` — FastAPI，薄封装 `query` / `analyze` / `discover`。知识库目录由环境变量 `PR_LEARNER_KNOWLEDGE_DIR` 覆盖（默认 `./knowledge`）。`GhError` → 400、`LlmError` → 502。**服务无内置鉴权**，仅面向本地/内网只读查询；公网暴露需前置网关加访问控制。渲染库走 `app.mount("/static/vendor", StaticFiles(...))`——**只挂 vendor 子目录，不挂整个 `static/`**（`static/` 是源码目录，以后往里放的东西不该自动变成公开 URL）；顺带的好处是目录缺失时 `StaticFiles` 构造即抛异常，「忘了下载 vendor」变成启动即失败而非页面白屏。`KnowledgeIn`/`DraftIn` 的 `content_format` 用 `Field(default="markdown")` + `mode="before"` validator 归一，**刻意不用 `Literal`**（那会让手写 `"HTML"` 的 agent 吃 422）；`create_knowledge` / `approve_draft` 里**绝不重新嗅探格式**——那是用户在页面上选过的值。
- `cli.py` — Typer 入口，把上述模块串成命令，是 agent 和人的统一操作面。`discover` 命令不调 LLM（agent 自己会写 GitHub 搜索语法）。
- `static/index.html` — Vue 3 单页，四个 tab：查找 PR / 分析 PR / 查询知识 / 待审草稿。依赖全部本地 vendor（`static/vendor/`，版本与 SHA256 见 `docs/vendor.md`），无构建步骤。LLM 凭证由前两个 tab 共享（`this.llm`），**只在内存里，不落 localStorage**。两个流式 tab 的日志、进行中状态、`ref` 与自动滚动目标各自独立——同名 `ref` 会互相覆盖，因为两个框都是 `v-show` 同时挂载。查找结果的排序（`sortedResults`）在前端做，不重新请求 gh；「价值」档必须原样返回后端顺序且**不能就地 sort**（会永久打乱模型排的顺序，切回来也回不去），日期缺失的条目两个方向都沉底。这段逻辑由 `tests/test_frontend_sort.py` 抽出真实函数体交给 node 执行来守。
- `static/index.html` 的富渲染（`tests/test_frontend_render.py` 同样抽真实函数体交给 node 守）：
  - `renderBody(content, contentFormat) -> {html, dropped}` 是唯一入口，html 分支直接 sanitize、markdown 分支先 `marked.parse`，两条共用同一份显式 `PURIFY_CONFIG`。整体 try/catch **永不抛异常**，库缺失或解析崩了降级成转义 `<pre class="fallback">`——它是在模板表达式里被调用的，抛错会让 Vue 连整个子树一起白屏。
  - DOMPurify 必须显式配白名单：**默认 profile 放行 `<style>` 标签和内联 `style=`**。`ALLOWED_ATTR` 显式列举已隐含禁掉所有 `on*`。class 按 `ALLOWED_CLASSES` 逐个过滤（DOMPurify 不支持按值过滤 class），这份名单必须与 `prompt_templates/analyze/html_rules.md` 逐项一致，由 `tests/test_prompt_html_contract.py` 钉住——不同步的话模型老实按规则写的 class 会被静默剥掉。
  - 调用点用自定义指令 `v-rich` 而不是 `v-html`：mermaid 会把 `<pre class='mermaid'>` 换成 `<svg>`，Vue 一旦重新 patch 就用 `_html` 覆盖回去、SVG 被抹掉。`updated` 里 `value !== oldValue` 才重建。
  - mermaid 用 `mermaid.render(id, src)` + 模块级 `Map<src, svg>` 缓存，**不用 `mermaid.run()`**（它靠 DOM 上的 `data-processed` 判重，而每次重建 innerHTML 标记就没了 → 必然重复渲染）。缓存命中是「打字时图不闪烁」的原因。`securityLevel:'strict'` 是刻意的信任边界：mermaid 输出的 SVG **不过我们的 sanitizer**（白名单里没有任何 SVG 标签）。3.5MB 的包懒加载，首次出现图表才下载。
  - 隐藏容器（`el.offsetParent === null`）先进 `pendingRich`，`watch: { tab }` 里 flush——分析完成后 `loadDrafts()` 时 tab 还是 `analyze`，草稿预览确实会在 `display:none` 下首次渲染。
  - 草稿页格式单选按钮的 `name` **必须带 draft id**（`:name="'fmt-' + d.id"`），否则多条草稿的单选组会合并成一组，改一条把别的都改了。`d._dropped` 那行「⚠ 已移除 N 处」是白名单违规唯一的可见信号（模型漏转义 `vector<int>` 会让代码**静默丢字**）；`approve` 请求体漏了 `content_format` 会让 HTML 草稿静默落成 `.md`。

数据流向单一：`discover → fetch → (agent 阅读) → store → query/api`。

### 提示词模板

提示词**不写死在代码里**，维护在 `prompt_templates/` 下的纯文本文件，用户和 agent 直接编辑即可：

```
prompt_templates/
  analyze/    system.md（{output_template} 与 {html_rules} 两个占位）  user.md（{pr_markdown}）
              output.json  html_rules.md
  discover/   query_system.md  query_user.md（{keyword} {repo}）
              rank_system.md（{output_template}）  rank_user.md（{keyword} {candidates}）  rank_output.json
```

改 `output.json` / `rank_output.json` 的字段名时须同步对应模块里的 `obj.get(...)` 读取键。
`html_rules.md` 是模型可用的 HTML 标签 / class / `language-XXX` 白名单，单独成文件而不是塞进
`system.md`（后者只有 9 行、可读性很好，塞 40 行白名单会毁掉它；而 class 表是最需要反复调、
要和前端 CSS 对齐的那部分）。**改它必须同步 `index.html` 里的 `ALLOWED_TAGS` / `ALLOWED_CLASSES`**，
`tests/test_prompt_html_contract.py` 会逐项核对，同时验证承诺的 `language-XXX` 真在 vendor 的
hljs 包里（包里没有的语言不报错，只是那块代码不着色——静默失效）。注意 hljs 没有 `cuda` 语言，
规则里让模型写 `cpp`，前端另有 `registerAliases(['cuda','cu','cuh'], {languageName:'cpp'})` 兜住。
新增子目录后注意 `pyproject.toml` 的 `artifacts` 用的是 `prompt_templates/**/*`——写成 `/*` 只匹配直接子项，本地 editable 安装照旧能读到、测试全绿，**只在 wheel / Docker 构建后炸**（`static/**/*` 同理，`static/vendor/` 不进 wheel 会让容器里 `StaticFiles` 构造即抛异常）。

## 知识库约定

- 每条知识一个文件：`knowledge/<分类slug>/<标题slug>.html`（分析产出的默认格式）或 `.md`（历史与手写），YAML frontmatter 含 `title / category / tags / source_pr / date / content_format`。
- **格式以扩展名为唯一权威**，frontmatter 的 `content_format` 只作自解释与兜底；未知值一律回落 `markdown`，永不抛异常。同标题换格式重存时旧扩展名那份会被删掉（否则 `load_all` 给出两条同 title 的知识、`index.md` 出现重复项）。
- HTML 正文是**片段**（无 `html/head/body`），受 `html_rules.md` 的白名单约束，检索时先 `strip_tags` 再匹配。
- 历史 `.md` 用 `pr-learner convert` 批量转（默认 dry-run）。转出来的 HTML 只有标题/列表/表格/代码块，**没有卡片、折叠和图表**——那些要重新走一次「分析 PR」才有。
- `knowledge/index.md` 由 `reindex` 自动生成，**不要手改**；它本身保持 Markdown（是给 GitHub / 编辑器看的仓库门面，不是给页面看的）。
- 知识库随 git 版本化，可挂进 docker 容器（`docker-compose.yml` 以只读方式挂载）。

## 部署

Dockerfile 基于 `uv` 官方镜像，`uv sync --no-dev` 装运行依赖，并 apt 安装 `gh`（容器内 `fetch` 拉 PR 需要）。知识库通过卷挂载而非打进镜像，本地更新即时生效。

容器内 `gh` 的 GitHub 凭证通过 `.env` 的 `GH_TOKEN` 注入（compose 自动加载）。**注意**：macOS 上 gh 的 token 存在系统钥匙串而非 `~/.config/gh`，仅只读挂载配置目录不足以让容器登录，必须走 `GH_TOKEN`。生成方式：`echo "GH_TOKEN=$(gh auth token)" > .env`。`.env` 已被 `.gitignore` 忽略，模板见 `.env.example`。
