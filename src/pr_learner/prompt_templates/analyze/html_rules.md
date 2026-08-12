# HTML 写法约定

`content` 是一段**HTML 片段**（不是完整网页：不要 `html` / `head` / `body`）。页面会先经过一层白名单消毒再内嵌渲染，**不在白名单里的标签、属性、class 会被静默剥掉**，所以请严格按下面写。

## 允许的标签

- 结构：`h2` `h3` `h4` `p` `ul` `ol` `li` `dl` `dt` `dd` `table` `thead` `tbody` `tr` `th` `td` `caption` `blockquote` `hr` `br` `div` `span` `section` `details` `summary` `figure` `figcaption`
- 行内强调：`strong` `em` `code` `kbd` `samp` `var` `sup` `sub` `del` `ins` `mark` `abbr` `a`
- 代码块：`<pre><code class='language-cpp'>…</code></pre>`
- 图表：`<pre class='mermaid'>graph TD; A[取数据] --> B[计算]</pre>`
- 图片：`<img src='https://…' alt='说明'>`，只允许 https 外链

不要用 `h1`：页面已经用自己的标题层级显示这条知识的标题。

## 硬性禁止

`script` `style` `iframe` `object` `embed` `form` `input` `button` `select` `textarea` `link` `meta` `base` `svg` `math` `template` `noscript` `audio` `video` `canvas`；任何 `on*` 事件属性；任何 `style=` 内联样式；任何 `data-*` 属性；`javascript:` / `data:` 链接；非 https 的图片。

（`svg` 由渲染 mermaid 的一方自己插入；`data-*` 会让抄来的 mermaid 例子带上 `data-processed`，导致图表根本不渲染。）

## 代码块语言

`class='language-XXX'` 的 XXX 只能取：`cpp` `c` `python` `bash` `diff` `json` `yaml` `rust` `go` `sql` `makefile` `xml` `markdown` `typescript` `javascript` `plaintext`。

**CUDA / CUDA C++ 一律写 `language-cpp`**（没有 cuda 这个语言），**Triton 写 `language-python`**。

## 允许的 class

只有这些可用，其余一律被剥掉：`card` `tip` `note` `warn` `danger` `success` `card-title` `cols` `col` `table-wrap` `metric` `metric-label` `metric-value` `takeaway` `kv` `center` `nowrap` `mermaid`

常用组合：

- 提示卡片：`<div class='card tip'><div class='card-title'>为什么快</div><p>…</p></div>`，语气档位有 tip / note / warn / danger / success 五种
- 并排小节：`<div class='cols'><div class='col'>…</div><div class='col'>…</div></div>`
- 宽表格：用 `<div class='table-wrap'><table>…</table></div>` 包一层，窄屏才能横向滚动
- 关键指标：`<div class='metric'><span class='metric-label'>吞吐</span><span class='metric-value'>+37%</span></div>`
- 全文结论：`<div class='takeaway'>…</div>`

## 两条写法要求（很重要）

1. **HTML 属性值用单引号**：`class='card tip'`。这段 HTML 要塞进 JSON 字符串里，双引号必须转义成 `\"`，是写坏 JSON 的头号原因。
2. **`<pre><code>` 里的 `<` `>` `&` 必须转义**成 `&lt;` `&gt;` `&amp;`。C++/CUDA 模板尤其容易漏：写 `vector&lt;int&gt;`、`template&lt;typename T&gt;`、`a &amp;&amp; b`。不转义的 `<int>` 会被当成未知标签整段删掉，**代码会静默少字**。

## 篇幅与信息密度

- 正文控制在 10000 字以内（**含 HTML 标签本身**，标签通常要吃掉三分之一左右）。信息密度优先，不要为凑长度注水。
- `<table>` 只用于真正的数据对比（基准/性能/精度/显存），不要用表格排版正文。
- 长推导、大段日志、完整 diff 放进 `<details><summary>展开看…</summary>…</details>`。
- PR 里有测试数据（基准、性能对比、精度指标、压测结果）时**必须**用 `<table>` 展示，并写清含义与结论。
