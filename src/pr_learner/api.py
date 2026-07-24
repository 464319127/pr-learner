"""知识查询 Web/API 服务（FastAPI）。

启动：uv run uvicorn pr_learner.api:app --host 0.0.0.0 --port 8000
知识库目录可用环境变量 PR_LEARNER_KNOWLEDGE_DIR 覆盖，默认 ./knowledge。

说明：本服务面向本地/内网查询与记录，未内置鉴权。若需暴露到公网，
请在前置网关(nginx/负载均衡)上加访问控制。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pr_learner import analyze as analyze_mod
from pr_learner import drafts as drafts_mod
from pr_learner import query as q
from pr_learner import store

KNOWLEDGE_DIR = Path(os.environ.get("PR_LEARNER_KNOWLEDGE_DIR", "knowledge"))
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="pr-learner 知识查询", version="0.1.0")


class KnowledgeIn(BaseModel):
    """提交一条知识的请求体（用户在页面 review 后的最终内容）。"""

    title: str = Field(..., min_length=1, description="知识标题")
    category: str = Field(..., min_length=1, description="分类")
    content: str = Field(..., min_length=1, description="正文 Markdown")
    tags: list[str] = Field(default_factory=list, description="标签")
    source_pr: str = Field(default="", description="来源 PR，如 owner/repo#123")


class DraftIn(BaseModel):
    """agent 分析 PR 后写入的待审草稿。字段与知识一致，供用户 review 修改。"""

    title: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_pr: str = Field(default="")


class AnalyzeIn(BaseModel):
    """页面提交 PR 分析请求：PR 地址 + 用户填写的 LLM 凭证。"""

    pr_url: str = Field(..., min_length=1, description="PR 链接或 owner/repo#123")
    api_key: str = Field(..., min_length=1, description="LLM API Key")
    model: str = Field(..., min_length=1, description="模型名")
    base_url: str = Field(
        default=analyze_mod.DEFAULT_BASE_URL, description="LLM 服务 base_url"
    )


@app.get("/api/categories")
def categories() -> dict[str, int]:
    """返回各分类的知识条数。"""
    return q.list_categories(KNOWLEDGE_DIR)


@app.get("/api/search")
def api_search(
    keyword: str | None = Query(default=None, description="关键词，匹配标题与正文"),
    category: str | None = Query(default=None, description="精确分类"),
    tag: str | None = Query(default=None, description="标签"),
) -> list[dict]:
    """按条件检索知识。"""
    return q.search(keyword, category=category, tag=tag, knowledge_dir=KNOWLEDGE_DIR)


@app.post("/api/knowledge")
def create_knowledge(item: KnowledgeIn) -> dict:
    """提交并保存一条知识，返回保存路径。"""
    if KNOWLEDGE_DIR.exists() and os.access(KNOWLEDGE_DIR, os.W_OK) is False:
        raise HTTPException(status_code=403, detail="知识库目录只读，无法写入")
    try:
        path = store.save_knowledge(
            item.title,
            item.category,
            item.content,
            tags=item.tags,
            source_pr=item.source_pr or None,
            knowledge_dir=KNOWLEDGE_DIR,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入失败: {exc}") from exc
    return {"path": str(path.relative_to(KNOWLEDGE_DIR)), "ok": True}


@app.get("/")
def index() -> FileResponse:
    """Vue 单页应用入口。"""
    return FileResponse(STATIC_DIR / "index.html")


# --- 草稿：agent 分析 PR 后生成，用户在页面 review 后转正 ---


@app.post("/api/analyze")
def analyze(item: AnalyzeIn) -> dict:
    """页面提交 PR 地址：拉取 → LLM 分析 → 存成待审草稿，返回草稿。

    api_key 仅用于本次请求转发给 LLM 服务，不落盘、不记录。
    """
    try:
        repo, number = analyze_mod.parse_pr_url(item.pr_url)
    except analyze_mod.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        fields = analyze_mod.analyze_pr(
            repo,
            number,
            api_key=item.api_key,
            model=item.model,
            base_url=item.base_url,
        )
    except analyze_mod.AnalyzeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    draft = drafts_mod.save_draft(
        fields["title"],
        fields["category"],
        fields["content"],
        tags=fields["tags"],
        source_pr=fields["source_pr"],
        knowledge_dir=KNOWLEDGE_DIR,
    )
    return draft


@app.get("/api/drafts")
def list_drafts() -> list[dict]:
    """列出全部待审草稿。"""
    return drafts_mod.list_drafts(KNOWLEDGE_DIR)


@app.post("/api/drafts")
def create_draft(item: DraftIn) -> dict:
    """agent 写入一条待审草稿。"""
    return drafts_mod.save_draft(
        item.title,
        item.category,
        item.content,
        tags=item.tags,
        source_pr=item.source_pr or None,
        knowledge_dir=KNOWLEDGE_DIR,
    )


@app.post("/api/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, item: KnowledgeIn) -> dict:
    """用户 review（可修改）后确认保存：转为正式知识并删除草稿。"""
    if not drafts_mod.get_draft(draft_id, KNOWLEDGE_DIR):
        raise HTTPException(status_code=404, detail="草稿不存在")
    path = store.save_knowledge(
        item.title,
        item.category,
        item.content,
        tags=item.tags,
        source_pr=item.source_pr or None,
        knowledge_dir=KNOWLEDGE_DIR,
    )
    drafts_mod.delete_draft(draft_id, KNOWLEDGE_DIR)
    return {"path": str(path.relative_to(KNOWLEDGE_DIR)), "ok": True}


@app.delete("/api/drafts/{draft_id}")
def discard_draft(draft_id: str) -> dict:
    """丢弃草稿。"""
    ok = drafts_mod.delete_draft(draft_id, KNOWLEDGE_DIR)
    if not ok:
        raise HTTPException(status_code=404, detail="草稿不存在")
    return {"ok": True}
