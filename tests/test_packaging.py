"""打包清单的回归测试：只在 wheel / Docker 构建后才炸的那类 bug。

`artifacts` 的 glob 写成 `static/*` 时只匹配直接子项，`static/vendor/` 整个子目录不会进
wheel——本地 editable 安装与全部测试照旧绿，起容器才发现 `StaticFiles` 构造即抛异常。
`markdown-it-py` 同理：它通过 typer→rich 已经在依赖树里，删掉声明也测不出来，直到某天
rich 换掉依赖，`convert` 命令 ImportError。这两条都只有在这里断言才有人管。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _cfg() -> dict:
    return tomllib.loads(PYPROJECT.read_text("utf-8"))


def test_static_artifacts_include_subdirectories() -> None:
    artifacts = _cfg()["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]
    assert "src/pr_learner/static/**/*" in artifacts, (
        "static 必须用 **/*，否则 static/vendor/ 不进 wheel，服务在容器里直接起不来"
    )
    assert "src/pr_learner/prompt_templates/**/*" in artifacts


def test_markdown_it_declared_explicitly() -> None:
    deps = _cfg()["project"]["dependencies"]
    assert any(d.startswith("markdown-it-py") for d in deps), (
        "convert 命令依赖 markdown-it-py；只靠 typer→rich 的传递依赖是脆的"
    )


def test_vendor_files_present() -> None:
    """vendor 缺文件时页面只是样式/图表失效（前端各处都有降级），不会有任何报错。"""
    vendor = Path(__file__).parent.parent / "src" / "pr_learner" / "static" / "vendor"
    expected = {
        "vue.global.prod.js",
        "marked.min.js",
        "purify.min.js",
        "highlight.min.js",
        "highlight-github-dark.min.css",
        "mermaid.min.js",
    }
    missing = {n for n in expected if not (vendor / n).is_file()}
    assert not missing, f"vendor 缺文件: {sorted(missing)}（下载方式见 docs/vendor.md）"
