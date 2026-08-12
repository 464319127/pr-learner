"""待审草稿存储：agent 分析 PR 后生成草稿，用户在页面 review 后转正入库。

草稿以 JSON 存在 <knowledge>/.drafts/ 下，与正式知识库隔离；`store.load_all` 显式跳过
任何以 `.` 开头的路径段，所以往这里放什么扩展名都不会串进正式知识库。草稿经用户在页面
确认（可编辑）后调用 store.save_knowledge 转为正式知识，随后删除草稿。

草稿带 `content_format`（`markdown` / `html`），决定页面怎么预览、approve 时落成什么
扩展名。旧草稿没有这个键，读取时补成 `markdown`——**不做嗅探**，它们按构造就是 markdown。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from pr_learner.store import (
    DEFAULT_CONTENT_FORMAT,
    DEFAULT_KNOWLEDGE_DIR,
    normalize_content_format,
)

DRAFTS_SUBDIR = ".drafts"


def _drafts_dir(knowledge_dir: Path) -> Path:
    return knowledge_dir / DRAFTS_SUBDIR


def _with_format(draft: dict) -> dict:
    """给旧草稿补上 content_format，就地返回同一个 dict。"""
    draft.setdefault("content_format", DEFAULT_CONTENT_FORMAT)
    return draft


def save_draft(
    title: str,
    category: str,
    content: str,
    *,
    tags: list[str] | None = None,
    source_pr: str | None = None,
    content_format: str = DEFAULT_CONTENT_FORMAT,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
) -> dict:
    """写入一条待审草稿，返回含 id 的草稿字典。"""
    d = _drafts_dir(knowledge_dir)
    d.mkdir(parents=True, exist_ok=True)
    draft = {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "category": category,
        "content": content,
        "content_format": normalize_content_format(content_format),
        "tags": tags or [],
        "source_pr": source_pr or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    (d / f"{draft['id']}.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return draft


def list_drafts(knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> list[dict]:
    """列出全部待审草稿，按创建时间倒序。"""
    d = _drafts_dir(knowledge_dir)
    if not d.exists():
        return []
    drafts = []
    for f in d.glob("*.json"):
        try:
            drafts.append(_with_format(json.loads(f.read_text("utf-8"))))
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(drafts, key=lambda x: x.get("created_at", ""), reverse=True)


def get_draft(draft_id: str, knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> dict | None:
    """按 id 读取单条草稿，不存在返回 None。"""
    f = _drafts_dir(knowledge_dir) / f"{draft_id}.json"
    if not f.exists():
        return None
    try:
        return _with_format(json.loads(f.read_text("utf-8")))
    except (json.JSONDecodeError, OSError):
        return None


def delete_draft(draft_id: str, knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> bool:
    """删除草稿，返回是否删除成功。"""
    f = _drafts_dir(knowledge_dir) / f"{draft_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False
