"""把阅读 PR 学到的知识写入分类 Markdown 知识库。

知识库结构：
    knowledge/
        <category>/<slug>.md   # 每条知识一个文件，带 YAML frontmatter
        index.md               # 自动生成的分类索引

每个知识文件的 frontmatter 记录标题、分类、标签、来源 PR、时间，
正文是 agent 总结的知识内容（Markdown）。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import frontmatter

DEFAULT_KNOWLEDGE_DIR = Path("knowledge")


def _slugify(text: str) -> str:
    """把标题转成文件名安全的 slug，保留中文。"""
    text = text.strip().lower()
    text = re.sub(r"[\s/\\]+", "-", text)
    text = re.sub(r"[^\w一-鿿-]", "", text)
    return text.strip("-") or "untitled"


def save_knowledge(
    title: str,
    category: str,
    content: str,
    *,
    tags: list[str] | None = None,
    source_pr: str | None = None,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> Path:
    """写入一条知识，返回文件路径。

    Args:
        title: 知识标题。
        category: 分类（作为子目录名）。
        content: 知识正文（Markdown）。
        tags: 标签列表。
        source_pr: 来源 PR，如 "owner/repo#123"。
        knowledge_dir: 知识库根目录。
    """
    category_slug = _slugify(category)
    target_dir = knowledge_dir / category_slug
    target_dir.mkdir(parents=True, exist_ok=True)

    post = frontmatter.Post(content)
    post["title"] = title
    post["category"] = category
    post["tags"] = tags or []
    post["source_pr"] = source_pr or ""
    post["date"] = date.today().isoformat()

    path = target_dir / f"{_slugify(title)}.md"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    rebuild_index(knowledge_dir)
    return path


def load_all(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> list[dict]:
    """加载知识库里所有知识，返回带 metadata 的字典列表。"""
    items: list[dict] = []
    if not knowledge_dir.exists():
        return items
    for md in sorted(knowledge_dir.rglob("*.md")):
        if md.name in {"index.md", "README.md"}:
            continue
        post = frontmatter.load(md)
        items.append(
            {
                "path": str(md.relative_to(knowledge_dir)),
                "title": post.get("title", md.stem),
                "category": post.get("category", md.parent.name),
                "tags": post.get("tags", []),
                "source_pr": post.get("source_pr", ""),
                "date": post.get("date", ""),
                "content": post.content,
            }
        )
    return items


def rebuild_index(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> Path:
    """按分类重建 index.md 索引。"""
    items = load_all(knowledge_dir)
    by_category: dict[str, list[dict]] = {}
    for it in items:
        by_category.setdefault(it["category"], []).append(it)

    lines = ["# 知识库索引", "", f"共 {len(items)} 条知识。", ""]
    for category in sorted(by_category):
        lines.append(f"## {category}")
        lines.append("")
        for it in sorted(by_category[category], key=lambda x: x["title"]):
            src = f" — 来源 {it['source_pr']}" if it["source_pr"] else ""
            lines.append(f"- [{it['title']}]({it['path']}){src}")
        lines.append("")

    index_path = knowledge_dir / "index.md"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path
