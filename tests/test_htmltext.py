"""htmltext 的纯函数测试：零 IO，跑得快，是本次改动最该有测试的地方。"""

from pr_learner import htmltext

# --- strip_tags：检索用的纯文本化 ---


def test_strip_tags_decodes_entities() -> None:
    """实体必须解码，否则正文里写 `&lt;` 的代码永远搜不到。"""
    text = htmltext.strip_tags("<p>a &amp; b &lt;int&gt; &#39;q&#39; x&nbsp;y</p>")
    assert "a & b <int> 'q'" in text
    assert "&amp;" not in text
    # nbsp 折叠成普通空格，否则「x y」搜不到
    assert "x y" in text


def test_strip_tags_ignores_gt_inside_attributes_and_comments() -> None:
    """属性值与注释里的 `>` 骗不到状态机——这是不用正则的主要理由之一。"""
    text = htmltext.strip_tags("<p title='a > b'>正文</p><!-- 注释里也有 > 号 -->")
    assert text == "正文"


def test_strip_tags_drops_script_and_style_keeps_pre() -> None:
    """script/style 正文丢弃；pre 里的文本保留（函数名/flag 名是高价值检索目标）。"""
    html = (
        "<style>.a{color:red}</style>"
        "<script>alert('x')</script>"
        "<pre><code class='language-cpp'>cudaMemcpyAsync(stream)</code></pre>"
    )
    text = htmltext.strip_tags(html)
    assert "color:red" not in text
    assert "alert" not in text
    assert "cudaMemcpyAsync(stream)" in text


def test_strip_tags_collapses_inline_tags() -> None:
    """行内标签折叠：这是修掉的那批假阴性。"""
    assert "内存泄漏" in htmltext.strip_tags("<p><strong>内存</strong>泄漏</p>")
    assert "内存泄漏" in htmltext.strip_tags("<p>内<code>存</code>泄漏</p>")


def test_strip_tags_separates_block_tags() -> None:
    """块级标签插换行：挡住 `内存</p><p>泄漏` 这种新的假阳性。"""
    text = htmltext.strip_tags("<p>内存</p><p>泄漏</p>")
    assert "内存泄漏" not in text
    assert "内存" in text and "泄漏" in text


def test_strip_tags_survives_malformed_html() -> None:
    """模型的半截输出不该让检索报错。"""
    for bad in ["<p>未闭合", "<div><span>a", "<<>>", "<p title=", ""]:
        assert isinstance(htmltext.strip_tags(bad), str)


# --- detect_content_format：只在模型没给格式时兜底，刻意偏向 markdown ---


def test_detect_markdown() -> None:
    assert htmltext.detect_content_format("## 标题\n正文") == "markdown"
    assert htmltext.detect_content_format("```cpp\nint a;\n```") == "markdown"
    assert htmltext.detect_content_format("- 一\n- 二") == "markdown"
    assert htmltext.detect_content_format("| a | b |\n|---|---|") == "markdown"
    assert htmltext.detect_content_format("") == "markdown"
    assert htmltext.detect_content_format("普通一段话") == "markdown"


def test_detect_details_wrapping_markdown_is_markdown() -> None:
    """GitHub 式 `<details>` 里包 markdown 是常见写法，不能误判成 html。"""
    src = "<details><summary>展开</summary>\n\n## 小标题\n\n- 项\n</details>"
    assert htmltext.detect_content_format(src) == "markdown"


def test_detect_html() -> None:
    assert htmltext.detect_content_format("<p>一</p><p>二</p>") == "html"
    assert (
        htmltext.detect_content_format(
            "<section><h2>标题</h2><table><tr><td>1</td></tr></table></section>"
        )
        == "html"
    )
    # 只有一个块级闭合标签时保守判成 markdown
    assert htmltext.detect_content_format("<p>就一段</p>") == "markdown"


# --- markdown_to_html：给 convert 命令用 ---


def test_markdown_to_html_fence_and_table() -> None:
    """fence 必须产出前端 highlight.js 认的 `language-` class。"""
    html = htmltext.markdown_to_html("```cpp\nint a = 1;\n```")
    assert '<pre><code class="language-cpp">' in html

    html = htmltext.markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html and "<td>" in html


def test_markdown_to_html_empty() -> None:
    assert htmltext.markdown_to_html("") == ""
