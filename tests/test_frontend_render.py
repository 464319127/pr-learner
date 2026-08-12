"""前端富渲染逻辑的回归测试：`renderBody` 的分派与降级、`keptClasses` 的 class 过滤。

这两段是纯 JS，Python 测不到，但各有一个后端完全看不见的失效模式：

- `renderBody` 一旦抛异常（vendor 没加载出来、正文把解析器搞崩），它是在模板表达式里被调用的，
  Vue 会连整个子树一起白屏——比「样式不对」严重得多，所以 try/catch 必须由测试钉住。
- `keptClasses` 放行了不该放行的 class，模型写个 `class='panel'` 就能套上应用外壳的边框背景，
  把预览区搞成页面错位。

做法沿用 `test_frontend_sort.py`：从 index.html 抽真实函数体交给 node 执行，不手抄逻辑。
`enhanceRich` 不在这里测——它依赖 DOM 和 mermaid，硬塞进 node 得造一堆假 DOM，不值。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行前端 JS")

INDEX_HTML = Path(__file__).parent.parent / "src" / "pr_learner" / "static" / "index.html"


def _snippet(pattern: str, what: str) -> str:
    m = re.search(pattern, INDEX_HTML.read_text("utf-8"))
    assert m, f"index.html 里找不到 {what}（改名了？同步更新本测试）"
    return m.group(1)


def _run(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(proc.stdout)


def _render_body(content: str, content_format: str, *, purify: str) -> dict:
    """在 node 里跑页面真实的 renderBody。`purify` 是 DOMPurify 替身的 JS 字面量。"""
    body = _snippet(r"renderBody\(content, contentFormat\) \{([\s\S]*?)\n        \},", "renderBody")
    escape_html = _snippet(r"(function escapeHtml\(s\) \{[\s\S]*?\n    \})", "escapeHtml")
    script = f"""
    {escape_html}
    const PURIFY_CONFIG = {{}};
    // marked 的替身：调过就留痕，用来证明 html 分支没有多走一遍 markdown 解析
    let markedCalls = 0;
    const marked = {{ parse: t => {{ markedCalls++; return '<md>' + t + '</md>'; }} }};
    const DOMPurify = {purify};
    const fn = new Function('return function(content, contentFormat){{'
      + {json.dumps(body)} + '}}')();
    const out = fn({json.dumps(content)}, {json.dumps(content_format)});
    console.log(JSON.stringify({{ ...out, markedCalls }}));
    """
    return _run(script)


_OK_PURIFY = "{ sanitize: (raw, cfg) => '<clean>' + raw + '</clean>', removed: [] }"


def test_html_branch_skips_markdown_parse() -> None:
    """HTML 正文不能再过一遍 marked：那会把裸 `<` 之外的东西也重新解释一轮。"""
    out = _render_body("<p>a</p>", "html", purify=_OK_PURIFY)
    assert out["html"] == "<clean><p>a</p></clean>"
    assert out["markedCalls"] == 0


def test_markdown_branch_goes_through_marked() -> None:
    out = _render_body("## a", "markdown", purify=_OK_PURIFY)
    assert out["html"] == "<clean><md>## a</md></clean>"
    assert out["markedCalls"] == 1


def test_unknown_format_falls_back_to_markdown() -> None:
    """格式字段脏了（旧草稿、手写 JSON）也要能渲染，不能空白。"""
    out = _render_body("## a", "", purify=_OK_PURIFY)
    assert out["markedCalls"] == 1


def test_dropped_counts_removed_nodes() -> None:
    """dropped 是草稿页那行「已移除 N 处」的唯一数据来源。"""
    purify = "{ sanitize: () => 'x', removed: [1, 2, 3] }"
    assert _render_body("<p>a</p>", "html", purify=purify)["dropped"] == 3


def test_sanitizer_failure_degrades_to_escaped_text() -> None:
    """消毒库挂了要显示转义原文。抛异常会让 Vue 整个子树白屏——这是最贵的失效。"""
    purify = "{ sanitize: () => { throw new Error('boom'); }, removed: [] }"
    out = _render_body("<script>bad</script>", "html", purify=purify)
    assert out["html"] == '<pre class="fallback">&lt;script&gt;bad&lt;/script&gt;</pre>'
    assert out["dropped"] == 0


def test_missing_library_degrades_too() -> None:
    """vendor 目录没下全时 DOMPurify 是 undefined，同样不能白屏。"""
    out = _render_body("<p>a & b</p>", "html", purify="undefined")
    assert out["html"] == '<pre class="fallback">&lt;p&gt;a &amp; b&lt;/p&gt;</pre>'


def _kept(value: str) -> list[str]:
    html = INDEX_HTML.read_text("utf-8")
    allowed = _snippet(r"(const ALLOWED_CLASSES = new Set\(\[[\s\S]*?\]\);)", "ALLOWED_CLASSES")
    kept = _snippet(r"(function keptClasses\(value\) \{[\s\S]*?\n    \})", "keptClasses")
    assert "ALLOWED_CLASSES" in html
    return _run(
        f"{allowed}\n{kept}\nconsole.log(JSON.stringify(keptClasses({json.dumps(value)})));"
    )


def test_whitelisted_classes_survive() -> None:
    assert _kept("card tip") == ["card", "tip"]


def test_language_classes_survive() -> None:
    """language-* 是前端 hljs 与提示词之间的契约，不在 18 个名字里但必须放行。"""
    assert _kept("language-cpp") == ["language-cpp"]
    assert _kept("language-c++") == ["language-c++"]


def test_shell_classes_are_stripped() -> None:
    """外壳的 class 全是全局选择器，漏进正文会把预览区套上应用外壳的边框背景。"""
    assert _kept("panel result tag open card") == ["card"]


def test_blank_and_garbage_class_values() -> None:
    assert _kept("") == []
    assert _kept("   ") == []
    assert _kept("Card CARD") == []  # 白名单区分大小写，别让 class 名蒙对
