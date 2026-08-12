# 0005 — 知识正文改用 HTML 片段

日期：2026-08-12

## 背景

知识正文原来是一整段 Markdown，落盘 `knowledge/<分类>/<标题>.md`，前端 `renderMd`
用 marked + DOMPurify 渲染。问题是 Markdown 的表达力封顶了：PR 知识里最需要的
性能对比表对齐、CUDA/C++ 代码高亮、内核执行流图示、长 diff 折叠、结论与踩坑的
卡片突出，Markdown 要么表达不了，要么渲染出来仍是一坨等宽文本。

三条决定方案形状的实测结论：

- `max_tokens=4096` **当时就已经是 bug**：现存 8 条知识正文最长 6898 字
  （≈4600~6900 token）已顶到上限，换 HTML 后正文还要涨 1.5~1.8 倍。
- `python-frontmatter` 存 HTML 无坑：`split` 用 `FM_BOUNDARY.split(text, 2)`，
  正文里的 `---` 行、甚至正文第一行就是 `---`，都能逐字节往返。
- DOMPurify 默认 profile **放行 `<style>` 标签和内联 `style=` 属性**
  （3.4.13 的 `freeze([...])` 白名单逐项确认过）。原先 `renderMd` 的零配置消毒
  本身就是个洞，显式白名单是安全修复而非洁癖。

## 决定

正文的第一等格式改成 **受限 HTML 片段**（不是完整网页）：提示词让模型直接产出，
落盘 `.html`，两个页面内嵌渲染并配代码高亮、mermaid、折叠与卡片样式。

| 维度 | 决定 |
|---|---|
| 存储 | `knowledge/<分类slug>/<标题slug>.html` = YAML frontmatter + HTML 片段；Markdown 继续支持 |
| 格式权威 | **扩展名是唯一权威**，frontmatter 的 `content_format` 只作自解释与兜底 |
| 字段取值 | 全链路统一叫 `content_format`，只有 `markdown` / `html`；未知值一律回落 `markdown`，永不抛异常 |
| 历史知识 | `pr-learner convert` 批量转换，默认 dry-run，`--apply` 才写盘 |
| 渲染依赖 | 6 个文件全部本地 vendor（见 `docs/vendor.md`），mermaid 懒加载 |
| 检索 | HTML 正文先 `strip_tags` 再匹配，`/api/search` 的 payload 一个字节不加 |
| 消毒 | DOMPurify 显式标签/属性/class 白名单 + 三个 hook |

## 理由

- **扩展名为权威，而不是 frontmatter**：文件名一眼可判、`ls` 就能看出格式，
  且两者冲突时不需要「谁赢」的规则。`content_format` 键仍然写进 frontmatter，
  是为了让单个文件离开仓库后仍能自解释。
- **新增 `htmltext.py` 而不是塞进 store/query**：持久层不该长出一个 HTML parser，
  检索层不该内联 parser。`strip_tags` 用 `html.parser` 而非正则：
  `convert_charrefs` 默认解掉 `&amp;/&lt;/&#39;/&nbsp;`（正则版必须自己补一遍且必漏数字实体），
  属性里的 `>` 和注释里的 `>` 骗不到状态机，模型的半截输出也不会让它抛异常。
- **`strip_tags` 的分隔符规则决定检索精度**：行内标签（`code strong em a span sup`…）
  边界插空串、块级标签插换行。原先 haystack 是原始 markdown，`**内存**泄漏`
  搜「内存泄漏」搜不到；折叠行内标签修掉这批假阴性，块级插换行挡住
  `内存</p><p>泄漏` 这种新假阳性，两边都不比原状差。
- **`detect_content_format` 宁可判成 markdown 也不误判成 html**：marked 本来放行行内 HTML，
  反向误判代价大得多。所以专门有一条「命中行首 markdown 块标记就判 markdown」，
  防 GitHub 式 `<details><summary>…</summary>` 里包 markdown 的常见写法被误判。
- **approve / create 不重新嗅探**：那里的值是用户在页面上明确选过的，再嗅探等于推翻用户的选择。
- **白名单单独成 `prompt_templates/analyze/html_rules.md`**：`system.md` 只有 9 行、
  可读性很好，塞 40 行白名单会毁掉它；而 class 表是最可能反复调（要和前端 CSS 对齐）的部分。
- **class 必须白名单而非「未知 class 自然失效」**：页面 CSS 的 `.panel`/`.tab`/`.result`/`.meta`/`.tag`/`.open`
  全是全局选择器，模型随手写个 `<div class='panel'>` 就会套上应用外壳的边框背景，
  把预览区搞成页面错位。18 个模型可用 class 与外壳的 class 已核对**零交集**。
- **提示词里两条写法要求是省 token / 少炸 JSON 的关键**：HTML 属性值用单引号
  （整段要塞进 JSON 字符串，双引号得转义，是弱模型写坏 JSON 的头号来源）；
  `<pre><code>` 里的 `< > &` 必须转义（C++/CUDA 模板的裸 `<int>` 会被浏览器当未知标签、
  被 DOMPurify 整个删掉，**代码文本静默丢字**）。
- **`max_tokens` 提到 20000 并可用 `PR_LEARNER_ANALYZE_MAX_TOKENS` 覆盖**：它管的是整个
  JSON 输出而不只是正文，而正文软上限是 10000 字（含标签）≈ 5600~8300 token，加其余字段
  约 6000~9000，20000 留 2~3 倍余量。这个数**只是上限不是预算**（按实际输出计费），调大
  不多花钱，调小则把「写超一点」从内容略长变成 JSON 截断、整次分析报错。反向代价是部分
  OpenAI 兼容模型的输出上限本身就是 16384/8192，超过会 400——所以必须能用环境变量下调。
  `llm.py` 的默认值不动——那是传输层，不该有业务判断。
- **不加 `GZipMiddleware`**：它会包住整个 app 包括两条 SSE，压缩流的缓冲会让
  「实时执行过程」卡住不刷新，正好毁掉 0003 的核心价值。`StaticFiles` 自带 etag/304 够了。

### 前端最容易出 bug 的三处

- **必须用自定义指令 `v-rich` 而不是 `v-html`**：mermaid 会把 `<pre class='mermaid'>`
  替换成 `<svg>`，而 Vue 只要重新 patch 就会用 `_html` 覆盖回去、SVG 被抹掉。
  「同一个东西既有 innerHTML 又要后处理」只有指令这一个自然载体。
  组件级 `updated()` 会被任何响应式变化触发（改标题、`_saving` 翻转、toast 定时器）
  且拿不到「哪个元素变了」；`ref` + 手动调则要在每个入口都记得调，漏一处就是幽灵 bug。
- **用 `mermaid.render(id, src)` 而不是 `mermaid.run({nodes})`**：`run` 靠 DOM 上的
  `data-processed` 判重，而每次重渲染都会重建 innerHTML、标记随之消失 → `run` 必然重复渲染。
  `render` 是纯函数式（源码进、SVG 字符串出），配模块级 `Map<src, svg>` 缓存才能真正判重：
  源码没变就同步命中缓存，**打字时零闪烁**。
- **mermaid 的 SVG 刻意不过我们的 sanitizer**（白名单里没有任何 SVG 标签，过一遍就全没了）。
  这是一处明示的信任边界：源码本身已是 sanitize 后 `<pre>` 里的纯文本，唯一注入面是
  mermaid 处理 label HTML 的方式，所以配 `securityLevel:'strict'` 把 label 里的 HTML 编码掉。
  另配 `suppressErrorRendering:true` + 逐块 catch：模型产的 mermaid 语法出错是常态，
  默认会往 DOM 里塞一个巨大的红色错误 SVG，改成「原始源码 + 一行红字」，用户正在 review 正好能手改。

### 折叠态放弃格式，是本次唯一的体验回退

原 `.content-preview` 的 `max-height:7em; overflow:hidden` 对任意 HTML 有三个具体问题：
`overflow:hidden` 会同时杀掉内部 `.table-wrap` 的横向滚动；一个 400px 高的 mermaid SVG
只露出 7em 的碎片；且 N 条结果全渲染 = 首屏就得下载 3.5MB mermaid 并渲染一堆没人看的图。
改成**折叠态纯文本摘录（`-webkit-line-clamp:3`）、展开才富渲染**，一举解决三件事。
摘录走 `{{ }}` 文本绑定不进 innerHTML，所以里面用廉价正则去标签是安全的（这是关键前提）。
代价是折叠态只剩三行纯文本；备选的 `mask-image` 渐隐治不了表格横滚和白渲染一堆图。

## 影响

- 新增 `htmltext.py`（`strip_tags` / `detect_content_format` / `markdown_to_html`）；
  `pyproject.toml` 显式声明 `markdown-it-py>=3`（它已通过 `typer → rich` 在依赖树里，
  但依赖一个传递依赖是脆的）。
- `store.py`：`save_knowledge` 加 **keyword-only 且有默认值**的 `content_format`
  （三个现存调用点与两条老测试一行未改就绿）；`load_all` 双 glob 合并后再 `sorted()`
  （分别 sorted 会变成「先全部 md 再全部 html」、顺序不稳），跳过 `index/README`
  与**任何以 `.` 开头的路径段**——这让「`load_all` 天然忽略 `.drafts/`」从巧合变成显式不变量；
  同标题跨格式重存时删掉旧扩展名那份，否则 `index.md` 会出现重复项。新增 `rewrite_as`
  原样搬运 frontmatter（`convert` 不能走 `save_knowledge`，它会把 `date` 重置成今天）。
- `api.py` 新增 `app.mount("/static/vendor", …)`。**只挂 vendor 子目录不挂整个 `static/`**：
  服务无鉴权，`static/` 是源码目录，以后往里放的东西不该自动变成公开 URL。
  附带好处：`StaticFiles` 目录不存在时构造即抛异常，「忘了下载 vendor」变成启动即失败而非页面白屏。
- `KnowledgeIn`/`DraftIn` 用 `Field(default="markdown")` + `mode="before"` validator，
  **不用 `Literal`**——那会让手写 `"HTML"` 的 agent 吃 422。
- CLI：`save` / `draft` 加 `--content-format`（默认 markdown，现有行为不变），新增 `convert`。
  诚实说明：转换出来的 HTML 只有标题/列表/表格/代码块，**没有卡片和折叠**——那要靠重新分析。
- 前端：`renderMd` → `renderBody(content, contentFormat) -> {html, dropped}`，
  两条分支共用同一份显式 DOMPurify config，整体 try/catch 永不抛异常
  （原先它在模板表达式里抛错会让 Vue 整个子树白屏）；`.markdown-body` 改名 `.rich-body`。
  草稿页加格式单选按钮（`name` 必须带 draft id，否则多条草稿的单选组会合并成一组）、
  `_dropped`「⚠ 已移除 N 处」提示——**这是整个方案里最划算的反馈回路**，
  模型漏转义 `<` 或发明新标签时，正在 review 的人当场看得见，
  否则白名单的所有违规都是静默的。
- 新增测试 `test_htmltext.py`、`test_frontend_render.py`、`test_packaging.py`、
  `test_prompt_html_contract.py`、`test_cli_convert.py`。其中
  `test_prompt_html_contract.py` 钉住三条静默失效的契约：提示词与前端的 class/标签白名单
  逐项一致、`ALLOWED_ATTR` 里没有 `style` 与 `on*`、承诺的 `language-XXX` 真在 vendor 的 hljs 包里。
- 遗留：`.md` 与 `.html` 长期并存（两条渲染路径都要维护）；`syncScroll` 在 HTML 下更不准
  （一张图能占掉预览区一半高度，但它本来就是近似，不值得为此加复杂度）；深色模式仍缺。
  均已记进 `TODO.md`。

## 取代关系

0001 关于「知识**正文**用 Markdown」的那部分被本记录取代；
0001 的其余决定（分类文件库、可 git、纯内存检索、query 层签名不变）**继续有效**。
