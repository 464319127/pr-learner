"""把阅读 PR 学到的知识写入分类知识库。

知识库结构：
    knowledge/
        <category>/<slug>.html # 每条知识一个文件，带 YAML frontmatter
        <category>/<slug>.md   # 历史知识仍是 Markdown，继续可读
        index.md               # 自动生成的分类索引（本身保持 Markdown）

每个知识文件的 frontmatter 记录标题、分类、标签、来源 PR、时间、正文格式，
正文是 agent 总结的知识内容。

**正文格式以扩展名为权威**：`.html` 就是 HTML 片段，`.md` 就是 Markdown。
frontmatter 里的 `content_format` 只作自解释与兜底，两者冲突时听扩展名的。
见 `docs/decisions/0005`。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import frontmatter

DEFAULT_KNOWLEDGE_DIR = Path("knowledge")

CONTENT_FORMATS = ("markdown", "html")
DEFAULT_CONTENT_FORMAT = "markdown"
_EXT_BY_FORMAT = {"markdown": ".md", "html": ".html"}
_FORMAT_BY_EXT = {".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html"}
# 能被认出来的格式写法。写宽一点：这个值可能来自模型输出或手写 JSON。
_FORMAT_ALIASES = {
    "md": "markdown",
    "markdown": "markdown",
    "text/markdown": "markdown",
    "html": "html",
    "htm": "html",
    "text/html": "html",
}

# 索引与说明文件不是知识，任何格式都跳过。
_SKIP_STEMS = {"index", "README", "readme"}


def _slugify(text: str) -> str:
    """把标题转成文件名安全的 slug，保留中文。"""
    text = text.strip().lower()
    text = re.sub(r"[\s/\\]+", "-", text)
    text = re.sub(r"[^\w一-鿿-]", "", text)
    return text.strip("-") or "untitled"


def normalize_content_format(value: object) -> str:
    """把任意输入归一成合法的正文格式，**永不抛异常**。

    未知值一律回落 `markdown`：这个值可能来自模型输出、手写 JSON、旧草稿，
    为一个格式字段炸掉一次分析或一次保存不值得（沿用 `discover.py` 的降级哲学）。
    """
    return _FORMAT_ALIASES.get(str(value or "").strip().lower(), DEFAULT_CONTENT_FORMAT)


def is_known_content_format(value: object) -> bool:
    """`value` 是否是能被认出来的格式别名。

    `normalize_content_format` 把未知值回落成 `markdown`，所以单看返回值分不清
    「模型明确说了 markdown」和「模型给了垃圾值」。`analyze` 要靠这个区分决定
    是否嗅探正文——别名表只有这一份，避免调用方各自抄一遍。
    """
    return str(value or "").strip().lower() in _FORMAT_ALIASES


def content_format_of_path(path: Path | str) -> str:
    """由扩展名判定正文格式——这是格式的**唯一权威来源**。"""
    return _FORMAT_BY_EXT.get(Path(path).suffix.lower(), DEFAULT_CONTENT_FORMAT)


def save_knowledge(
    title: str,
    category: str,
    content: str,
    *,
    tags: list[str] | None = None,
    source_pr: str | None = None,
    content_format: str = DEFAULT_CONTENT_FORMAT,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> Path:
    """写入一条知识，返回文件路径。

    Args:
        title: 知识标题。
        category: 分类（作为子目录名）。
        content: 知识正文。
        tags: 标签列表。
        source_pr: 来源 PR，如 "owner/repo#123"。
        content_format: `markdown` 或 `html`，决定扩展名；未知值回落 markdown。
        knowledge_dir: 知识库根目录。
    """
    fmt = normalize_content_format(content_format)
    category_slug = _slugify(category)
    target_dir = knowledge_dir / category_slug
    target_dir.mkdir(parents=True, exist_ok=True)

    post = frontmatter.Post(content)
    post["title"] = title
    post["category"] = category
    post["tags"] = tags or []
    post["source_pr"] = source_pr or ""
    post["date"] = date.today().isoformat()
    post["content_format"] = fmt

    stem = _slugify(title)
    path = target_dir / f"{stem}{_EXT_BY_FORMAT[fmt]}"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    # 同一条知识换了格式时必须删掉另一个扩展名的旧文件，否则 load_all 会给出两条同名
    # 知识、index.md 出现重复项。
    for other_ext in _EXT_BY_FORMAT.values():
        stale = target_dir / f"{stem}{other_ext}"
        if stale != path and stale.exists():
            stale.unlink()
    rebuild_index(knowledge_dir)
    return path


def rewrite_as(path: Path, content: str, content_format: str) -> Path:
    """原地换格式重写一条知识：搬运**全部**原 frontmatter 键，只改正文与扩展名。

    给 `pr-learner convert` 用，不走 `save_knowledge`——后者会把 `date` 重置成今天，
    历史知识的日期不该因为一次格式转换而丢失。
    """
    fmt = normalize_content_format(content_format)
    post = frontmatter.load(path)
    post.content = content
    post["content_format"] = fmt

    new_path = path.with_suffix(_EXT_BY_FORMAT[fmt])
    new_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    if new_path != path:
        path.unlink()
    return new_path


def load_all(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> list[dict]:
    """加载知识库里所有知识，返回带 metadata 的字典列表。

    **只认 `.md` 与 `.html`，且跳过任何以 `.` 开头的路径段**——后者让
    `knowledge/.drafts/` 里的待审草稿被排除成一条显式不变量，而不是「碰巧只扫 *.md」。
    """
    items: list[dict] = []
    if not knowledge_dir.exists():
        return items
    # 两个 glob 的结果**合并后再排序**：分别 sorted 会变成「先全部 md 再全部 html」，
    # 同一分类下的顺序随格式跳动。
    files = [p for pattern in ("*.md", "*.html") for p in knowledge_dir.rglob(pattern)]
    for doc in sorted(files):
        rel = doc.relative_to(knowledge_dir)
        if doc.stem in _SKIP_STEMS or any(part.startswith(".") for part in rel.parts):
            continue
        post = frontmatter.load(doc)
        items.append(
            {
                "path": str(rel),
                "title": post.get("title", doc.stem),
                "category": post.get("category", doc.parent.name),
                "tags": post.get("tags", []),
                "source_pr": post.get("source_pr", ""),
                "date": post.get("date", ""),
                # 扩展名为权威，不读 frontmatter 里的同名键
                "content_format": content_format_of_path(doc),
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
