"""正文格式的检测、纯文本化与 Markdown→HTML 转换。

知识正文有两种格式：`markdown` 与 `html`（见 `docs/decisions/0005`）。这三个纯函数被
store / query / analyze / cli 共用——持久层不该长出一个 HTML parser，检索层也不该内联
一个，所以单独成模块。

约定：格式值只有 `markdown` / `html` 两种，未知值一律回落 `markdown`，永不抛异常。
"""

from __future__ import annotations

import contextlib
import re
from html.parser import HTMLParser

# 行内标签：进出都**不插分隔符**。今天检索的 haystack 是原始正文，
# `**内存**泄漏` 搜「内存泄漏」搜不到；折叠行内标签正是为了修掉这批假阴性。
_INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "code",
        "del",
        "dfn",
        "em",
        "i",
        "ins",
        "kbd",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    }
)

# 正文整段丢弃：输入是模型原始输出，不能假设它们不存在。
# 注意 `<pre>` **不在**这里——代码块里的函数名/flag 名是高价值检索目标。
_DROP_TAGS = frozenset({"script", "style"})

# 字符类里除了空格与制表符，还有两个**不可见字符**：nbsp(U+00A0) 与全角空格(U+3000)，
# 它们由 `&nbsp;` / 全角排版解码而来，不折叠掉会让关键词匹配莫名失败。
_SPACES_RE = re.compile(r"[ \t 　]+")

# markdown 的块级标记。命中任意一条就判 markdown——见 detect_content_format 的偏向说明。
_MD_BLOCK_RE = re.compile(r"^(?:#{1,6} |```|~~~|[-*+] |\d+\. |> |\|)", re.MULTILINE)

# HTML 块级闭合标签。用闭合而非开始标签：`<int>` 这种伪标签不会有配对的闭合。
_CLOSING_BLOCK_RE = re.compile(
    r"</(?:p|div|section|article|h[1-6]|ul|ol|li|dl|dt|dd|table|thead|tbody|tfoot"
    r"|tr|td|th|caption|pre|blockquote|details|summary|figure|figcaption)\s*>",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """把 HTML 抽成纯文本。

    用状态机而不是正则，三个理由都在实际输入上遇到过：
    1. `convert_charrefs=True` 默认解掉 `&amp;` `&lt;` `&#39;` `&nbsp;`（正则版必须自己
       补一遍实体表，且几乎必漏数字实体）；
    2. 属性值里的 `>`（`title='a > b'`）和 HTML 注释里的 `>` 骗不到状态机；
    3. 对模型半截输出这类畸形 HTML 不抛异常。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if tag not in _INLINE_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag not in _INLINE_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        """收尾：块级标签插入的换行在这里归一化成可检索的纯文本。"""
        lines = (_SPACES_RE.sub(" ", ln).strip() for ln in "".join(self._parts).splitlines())
        return "\n".join(ln for ln in lines if ln)


def strip_tags(html: str) -> str:
    """把 HTML 正文抽成纯文本，供关键词检索使用。

    块级标签边界插换行、行内标签折叠：前者挡住 `内存</p><p>泄漏` 被搜成「内存泄漏」的
    假阳性，后者修掉 `<strong>内存</strong>泄漏` 搜不到的假阴性。
    """
    if not html:
        return ""
    parser = _TextExtractor()
    # 畸形 HTML（模型的半截输出）不该让检索报错；已经抽到的部分照常返回。
    with contextlib.suppress(Exception):
        parser.feed(html)
        parser.close()
    return parser.text()


def detect_content_format(content: str) -> str:
    """猜正文格式。只在「模型没给 content_format」时兜底。

    **刻意偏向 markdown**：marked 本来就放行行内 HTML，把 html 判成 markdown 顶多样式
    略歪；反过来把 markdown 判成 html 会让 `## 标题` 原样显示成一行文本，代价大得多。
    """
    text = (content or "").strip()
    if not text.startswith("<"):
        return "markdown"
    # GitHub 式 `<details><summary>…</summary>` 里包 markdown 是很常见的写法，
    # 这一条专门防它被误判成 html。
    if _MD_BLOCK_RE.search(text):
        return "markdown"
    if len(_CLOSING_BLOCK_RE.findall(text)) >= 2:
        return "html"
    return "markdown"


def markdown_to_html(md_text: str) -> str:
    """把 Markdown 正文转成 HTML 片段，给 `pr-learner convert` 用。

    fence 渲染器天然产出 `<pre><code class="language-cpp">`，正好对上前端 highlight.js
    的契约。**转换产物只有标题/列表/表格/代码块，没有卡片和折叠**——那部分要靠重新分析。
    """
    # 延迟导入：只有 convert 命令需要，不让 store/query 的 import 顺带拖上它。
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return md.render(md_text or "")
