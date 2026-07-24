"""知识库检索：按分类、标签、关键词过滤。

纯内存扫描 Markdown 文件，适合中小规模知识库；数据量变大后
可替换为 SQLite/全文索引，接口保持不变。
"""

from __future__ import annotations

from pathlib import Path

from pr_learner.store import DEFAULT_KNOWLEDGE_DIR, load_all


def search(
    keyword: str | None = None,
    *,
    category: str | None = None,
    tag: str | None = None,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> list[dict]:
    """按条件检索知识。多个条件之间为 AND 关系。

    Args:
        keyword: 在标题和正文中做大小写不敏感的子串匹配。
        category: 精确匹配分类。
        tag: 匹配标签列表中的某一项。
        knowledge_dir: 知识库根目录。
    """
    items = load_all(knowledge_dir)
    kw = keyword.lower() if keyword else None

    def match(it: dict) -> bool:
        if category and it["category"] != category:
            return False
        if tag and tag not in (it.get("tags") or []):
            return False
        if kw:
            haystack = f"{it['title']}\n{it['content']}".lower()
            if kw not in haystack:
                return False
        return True

    return [it for it in items if match(it)]


def list_categories(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> dict[str, int]:
    """返回每个分类下的知识条数。"""
    counts: dict[str, int] = {}
    for it in load_all(knowledge_dir):
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    return counts
