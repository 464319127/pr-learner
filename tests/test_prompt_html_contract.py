"""提示词与前端消毒白名单的契约测试。

模型按 `analyze/html_rules.md` 写 HTML，前端按 `index.html` 里的 JS 白名单过滤标签、属性与
class。两处不同步 = 模型老实按规则写的东西被静默剥掉，页面样式莫名不生效、代码块甚至会少字，
而且没有任何报错。这个文件就是钉住这条契约的。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pr_learner import analyze, prompts

INDEX_HTML = Path(__file__).parent.parent / "src" / "pr_learner" / "static" / "index.html"
HLJS = INDEX_HTML.parent / "vendor" / "highlight.min.js"


def _rules_classes() -> set[str]:
    """从 html_rules.md 里「只有这些可用」那一行抠出 class 白名单。"""
    text = prompts.read("analyze/html_rules.md")
    line = next(ln for ln in text.splitlines() if "只有这些可用" in ln)
    return set(re.findall(r"`([a-z][a-z-]*)`", line))


def _frontend_classes() -> set[str]:
    """从 index.html 的 ALLOWED_CLASSES 数组里抠出同一份白名单。"""
    src = INDEX_HTML.read_text("utf-8")
    m = re.search(r"ALLOWED_CLASSES\s*=\s*new Set\(\s*(\[[^\]]*\])", src)
    assert m, "index.html 里没找到 ALLOWED_CLASSES = new Set([...])"
    return set(json.loads(m.group(1).replace("'", '"')))


def test_html_rules_template_readable() -> None:
    assert "HTML 写法约定" in prompts.read("analyze/html_rules.md")


def test_system_prompt_fills_both_placeholders() -> None:
    """漏填占位是 str.replace 方案的典型故障，必须由测试盯住。"""
    sp = analyze.build_system_prompt()
    assert "{output_template}" not in sp
    assert "{html_rules}" not in sp
    assert '"content_format"' in sp  # 输出模板已嵌入
    assert "允许的 class" in sp  # HTML 约定已嵌入


def test_output_template_declares_content_format() -> None:
    obj = json.loads(prompts.read("analyze/output.json"))
    assert obj["content_format"] == "html"
    assert "HTML" in obj["content"]


def test_class_whitelists_match_frontend() -> None:
    assert _rules_classes() == _frontend_classes()


def _names(line: str) -> set[str]:
    """抠出一行里所有反引号包着的小写标签名（`on*` / `style=` / `data-*` 这类不会被算进来）。"""
    return set(re.findall(r"`([a-z][a-z0-9]*)`", line))


def _rules_lines(marker: str) -> str:
    return next(ln for ln in prompts.read("analyze/html_rules.md").splitlines() if marker in ln)


def _frontend_tags() -> set[str]:
    src = INDEX_HTML.read_text("utf-8")
    m = re.search(r"ALLOWED_TAGS:\s*(\[[^\]]*\])", src)
    assert m, "index.html 里没找到 ALLOWED_TAGS: [...]"
    return set(json.loads(m.group(1).replace("'", '"')))


def test_allowed_tags_cover_the_rules() -> None:
    """规则里推荐的标签必须全在前端白名单里，否则模型照着写就被整段删掉。"""
    promised = _names(_rules_lines("- 结构：")) | _names(_rules_lines("- 行内强调："))
    missing = promised - _frontend_tags()
    assert not missing, f"html_rules.md 推荐了前端会剥掉的标签: {sorted(missing)}"
    # 代码块与图表的载体单独确认：它们不在上面两行里，但整套渲染都靠它们
    assert {"pre", "code", "img"} <= _frontend_tags()


def test_forbidden_tags_are_really_forbidden() -> None:
    """禁止清单与前端白名单必须零交集，否则「硬性禁止」只是句空话。"""
    # 禁止清单是「硬性禁止」小节那一整段，用它开头的两个标签名定位
    overlap = _names(_rules_lines("`script` `style`")) & _frontend_tags()
    assert not overlap, f"禁止清单里的标签竟在前端白名单里: {sorted(overlap)}"


def test_style_attribute_not_allowed() -> None:
    """DOMPurify 默认放行内联 style=，显式白名单里必须没有它（这是安全修复，不是洁癖）。"""
    src = INDEX_HTML.read_text("utf-8")
    m = re.search(r"ALLOWED_ATTR:\s*(\[[^\]]*\])", src)
    assert m, "index.html 里没找到 ALLOWED_ATTR: [...]"
    attrs = set(json.loads(m.group(1).replace("'", '"')))
    assert "style" not in attrs
    assert not {a for a in attrs if a.startswith("on")}
    assert {"class", "href", "src"} <= attrs


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能加载 highlight.js")
def test_promised_languages_exist_in_bundle() -> None:
    """html_rules.md 承诺的 language-XXX 必须真在 vendor 的 hljs 包里。

    包里没有的语言不会报错，只是那块代码不着色——静默失效，只有这里能拦住。
    """
    promised = _names(_rules_lines("的 XXX 只能取"))
    script = (
        f"const hljs = require({json.dumps(str(HLJS))});"
        "hljs.registerAliases(['cuda'], { languageName: 'cpp' });"
        "console.log(JSON.stringify(hljs.listLanguages().concat(['cuda'])));"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    langs = set(json.loads(proc.stdout))
    assert promised <= langs, f"hljs 包里没有这些语言: {sorted(promised - langs)}"


def test_cuda_aliases_registered_in_frontend() -> None:
    """规则让模型把 CUDA 写成 language-cpp，前端还得兜住不听话的那次。"""
    src = INDEX_HTML.read_text("utf-8")
    assert re.search(r"registerAliases\(\[[^\]]*'cuda'[^\]]*\]", src), (
        "index.html 里没给 cuda 注册别名：hljs 没有 cuda 语言，模型写了就不着色"
    )
