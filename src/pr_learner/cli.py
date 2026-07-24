"""命令行入口，把 fetch / store / query 串起来供 agent 或人使用。

示例：
    uv run pr-learner fetch owner/repo 123      # 拉取 PR 并输出 Markdown 供阅读
    uv run pr-learner save "标题" 分类 内容.md   # 保存一条知识
    uv run pr-learner search --keyword 重试       # 检索知识
    uv run pr-learner categories                 # 查看分类统计
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from pr_learner import drafts as drafts_mod
from pr_learner import fetch as fetch_mod
from pr_learner import query as query_mod
from pr_learner import store as store_mod

app = typer.Typer(help="pr-learner：阅读 GitHub PR 并分类记录知识", add_completion=False)


@app.command()
def fetch(
    repo: str = typer.Argument(..., help="owner/name 格式的仓库"),
    number: int = typer.Argument(..., help="PR 编号"),
) -> None:
    """拉取 PR 并以 Markdown 输出到 stdout，供 agent 直接阅读。"""
    pr = fetch_mod.fetch_pr(repo, number)
    typer.echo(fetch_mod.to_markdown(pr))


@app.command()
def save(
    title: str = typer.Argument(..., help="知识标题"),
    category: str = typer.Argument(..., help="分类"),
    content_file: Path = typer.Argument(..., help="知识正文文件；传 - 表示从 stdin 读取"),
    tags: str = typer.Option("", help="逗号分隔的标签"),
    source_pr: str = typer.Option("", help="来源 PR，如 owner/repo#123"),
) -> None:
    """保存一条知识到分类知识库。"""
    content = sys.stdin.read() if str(content_file) == "-" else content_file.read_text("utf-8")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    path = store_mod.save_knowledge(
        title, category, content, tags=tag_list, source_pr=source_pr or None
    )
    typer.echo(f"已保存: {path}")


@app.command()
def draft(
    title: str = typer.Argument(..., help="知识标题"),
    category: str = typer.Argument(..., help="分类"),
    content_file: Path = typer.Argument(..., help="知识正文文件；传 - 表示从 stdin 读取"),
    tags: str = typer.Option("", help="逗号分隔的标签"),
    source_pr: str = typer.Option("", help="来源 PR，如 owner/repo#123"),
) -> None:
    """写入一条待审草稿，供用户在页面 review 后确认保存。

    这是 agent 分析完 PR 后的推荐入口：生成草稿，用户在 Web 页面修改并保存。
    """
    content = sys.stdin.read() if str(content_file) == "-" else content_file.read_text("utf-8")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    d = drafts_mod.save_draft(
        title, category, content, tags=tag_list, source_pr=source_pr or None
    )
    typer.echo(f"已生成草稿 {d['id']}，请到页面 review：http://localhost:8000")


@app.command()
def search(
    keyword: str = typer.Option(None, help="关键词"),
    category: str = typer.Option(None, help="分类"),
    tag: str = typer.Option(None, help="标签"),
) -> None:
    """检索知识，输出匹配的标题与路径。"""
    results = query_mod.search(keyword, category=category, tag=tag)
    if not results:
        typer.echo("无匹配结果")
        raise typer.Exit()
    for it in results:
        typer.echo(f"[{it['category']}] {it['title']} -> {it['path']}")


@app.command()
def categories() -> None:
    """查看各分类的知识条数。"""
    for cat, n in sorted(query_mod.list_categories().items()):
        typer.echo(f"{cat}: {n}")


@app.command()
def reindex() -> None:
    """重建知识库索引 index.md。"""
    path = store_mod.rebuild_index()
    typer.echo(f"已重建索引: {path}")


if __name__ == "__main__":
    app()
