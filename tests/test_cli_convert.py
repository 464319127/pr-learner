"""`pr-learner convert` 的测试：dry-run 不写盘，--apply 保留原 date。

这个命令会批量重写知识库文件，是全 CLI 里唯一有破坏性的一条，所以两种模式都要钉住。
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
from typer.testing import CliRunner

from pr_learner import store
from pr_learner.cli import app

runner = CliRunner()


def _seed(kdir: Path) -> Path:
    path = store.save_knowledge(
        "内核融合",
        "cuda",
        "## 结论\n\n- 省掉一次全局内存往返\n\n```cpp\nvector<int> v;\n```",
        tags=["cuda"],
        source_pr="o/r#1",
        knowledge_dir=kdir,
    )
    post = frontmatter.load(path)
    post["date"] = "2020-01-02"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return path


def test_convert_dry_run_writes_nothing(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    path = _seed(kdir)

    res = runner.invoke(app, ["convert", "--knowledge-dir", str(kdir)])
    assert res.exit_code == 0, res.output
    assert "将转换" in res.output and "dry-run" in res.output
    assert path.exists()
    assert not path.with_suffix(".html").exists()


def test_convert_apply_keeps_frontmatter_and_reindexes(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    path = _seed(kdir)

    res = runner.invoke(app, ["convert", "--knowledge-dir", str(kdir), "--apply"])
    assert res.exit_code == 0, res.output

    html_path = path.with_suffix(".html")
    assert html_path.exists()
    assert not path.exists()

    post = frontmatter.load(html_path)
    assert post["date"] == "2020-01-02"  # 格式转换不该把历史日期改成今天
    assert post["content_format"] == "html"
    assert post["tags"] == ["cuda"]
    # markdown-it 的 fence 渲染器产出的 class 正是前端 hljs 依赖的契约
    assert '<pre><code class="language-cpp">' in post.content
    assert "index.md" not in post.content

    index = (kdir / "index.md").read_text("utf-8")
    assert "内核融合.html" in index


def test_convert_skips_when_html_exists(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    path = _seed(kdir)
    path.with_suffix(".html").write_text("---\ntitle: 占位\n---\n<p>x</p>", encoding="utf-8")

    res = runner.invoke(app, ["convert", "--knowledge-dir", str(kdir), "--apply"])
    assert res.exit_code == 0, res.output
    assert "跳过" in res.output
    assert path.exists()  # 原 .md 保持不动


def test_convert_reports_nothing_to_do(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    store.save_knowledge(
        "只有 html", "cuda", "<p>a</p><p>b</p>", content_format="html", knowledge_dir=kdir
    )
    res = runner.invoke(app, ["convert", "--knowledge-dir", str(kdir)])
    assert res.exit_code == 0, res.output
    assert "没有需要转换" in res.output
