"""待审草稿存储：agent 分析 PR 后生成草稿，用户在页面 review 后转正入库。

草稿以 JSON 存在 <knowledge>/.drafts/ 下，与正式知识库隔离；load_all 只扫描
*.md，天然忽略草稿。草稿经用户在页面确认（可编辑）后调用 store.save_knowledge
转为正式知识，随后删除草稿。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from pr_learner.store import DEFAULT_KNOWLEDGE_DIR

DRAFTS_SUBDIR = ".drafts"


def _drafts_dir(knowledge_dir: Path) -> Path:
    return knowledge_dir / DRAFTS_SUBDIR


def save_draft(
    title: str,
    category: str,
    content: str,
    *,
    tags: list[str] | None = None,
    source_pr: str | None = None,
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
            drafts.append(json.loads(f.read_text("utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(drafts, key=lambda x: x.get("created_at", ""), reverse=True)


def get_draft(draft_id: str, knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> dict | None:
    """按 id 读取单条草稿，不存在返回 None。"""
    f = _drafts_dir(knowledge_dir) / f"{draft_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete_draft(draft_id: str, knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR) -> bool:
    """删除草稿，返回是否删除成功。"""
    f = _drafts_dir(knowledge_dir) / f"{draft_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False
