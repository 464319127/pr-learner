"""知识查询 Web/API 服务（FastAPI）。

启动：uv run uvicorn pr_learner.api:app --host 0.0.0.0 --port 8000
知识库目录可用环境变量 PR_LEARNER_KNOWLEDGE_DIR 覆盖，默认 ./knowledge。

说明：本服务面向本地/内网查询与记录，未内置鉴权。若需暴露到公网，
请在前置网关(nginx/负载均衡)上加访问控制。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from pr_learner import analyze as analyze_mod
from pr_learner import discover as discover_mod
from pr_learner import drafts as drafts_mod
from pr_learner import query as q
from pr_learner import store
from pr_learner.fetch import GhError
from pr_learner.llm import LlmError

KNOWLEDGE_DIR = Path(os.environ.get("PR_LEARNER_KNOWLEDGE_DIR", "knowledge"))
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="pr-learner 知识查询", version="0.1.0")

# 前端依赖（vue / marked / DOMPurify / highlight.js / mermaid）全部本地 vendor。
# 只挂 vendor 子目录，**不挂整个 static/**：本服务无鉴权，static/ 是源码目录，
# 以后往里放的东西不该自动变成公开 URL。目录不存在时 StaticFiles 构造即抛异常，
# 「忘了下载 vendor」于是变成启动即失败，而不是页面白屏。
app.mount(
    "/static/vendor",
    StaticFiles(directory=STATIC_DIR / "vendor"),
    name="vendor",
)


def _sse(evt: dict) -> str:
    """把一个事件 dict 编码成一行 SSE。"""
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


class KnowledgeIn(BaseModel):
    """提交一条知识的请求体（用户在页面 review 后的最终内容）。"""

    title: str = Field(..., min_length=1, description="知识标题")
    category: str = Field(..., min_length=1, description="分类")
    content: str = Field(..., min_length=1, description="正文 Markdown 或 HTML 片段")
    tags: list[str] = Field(default_factory=list, description="标签")
    source_pr: str = Field(default="", description="来源 PR，如 owner/repo#123")
    content_format: str = Field(
        default=store.DEFAULT_CONTENT_FORMAT, description="markdown / html，决定落盘扩展名"
    )

    # 不用 Literal["markdown","html"]：那会让手写 "HTML" 的 agent 吃 422。
    # 归一化到合法值更符合本项目「降级而不报错」的一贯做法。
    _norm_format = field_validator("content_format", mode="before")(
        lambda v: store.normalize_content_format(v)
    )


class DraftIn(BaseModel):
    """agent 分析 PR 后写入的待审草稿。字段与知识一致，供用户 review 修改。"""

    title: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_pr: str = Field(default="")
    content_format: str = Field(default=store.DEFAULT_CONTENT_FORMAT)

    _norm_format = field_validator("content_format", mode="before")(
        lambda v: store.normalize_content_format(v)
    )


class AnalyzeIn(BaseModel):
    """页面提交 PR 分析请求：PR 地址 + 用户填写的 LLM 凭证。"""

    pr_url: str = Field(..., min_length=1, description="PR 链接或 owner/repo#123")
    api_key: str = Field(..., min_length=1, description="LLM API Key")
    model: str = Field(..., min_length=1, description="模型名")
    base_url: str = Field(
        default=analyze_mod.DEFAULT_BASE_URL, description="LLM 服务 base_url"
    )


class PrDiscoverIn(BaseModel):
    """页面提交 PR 查找请求：关键词 + 仓库名 + LLM 凭证。

    api_key 留空时降级为纯 gh 搜索（不调模型），但中文关键词在这种模式下
    大概率搜不到——GitHub 不索引中文。
    """

    keyword: str = Field(default="", description="自然语言关键词，可中文")
    repo: str = Field(default="", description="owner/name 格式的仓库")
    limit: int = Field(default=20, ge=1, le=100, description="最多返回条数")
    state: str | None = Field(
        default=None, description="open / merged / closed（closed 指已关闭未合并）"
    )
    api_key: str = Field(default="", description="LLM API Key，留空则不调模型")
    model: str = Field(default="", description="模型名")
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
            content_format=item.content_format,
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
    except GhError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except analyze_mod.AnalyzeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    draft = drafts_mod.save_draft(
        fields["title"],
        fields["category"],
        fields["content"],
        tags=fields["tags"],
        source_pr=fields["source_pr"],
        content_format=fields["content_format"],
        knowledge_dir=KNOWLEDGE_DIR,
    )
    return draft


@app.post("/api/analyze/stream")
def analyze_stream(item: AnalyzeIn) -> StreamingResponse:
    """流式分析 PR：以 SSE 逐段推送思考过程与模型输出，结束时落库草稿。

    页面用 fetch + ReadableStream 逐行读取，实时滚动展示，让用户看到执行进度。
    每个 SSE 事件是一行 `data: <json>\\n\\n`，json 的 type 见 analyze.analyze_pr_stream。
    api_key 仅用于本次请求转发给 LLM，不落盘、不记录。
    """
    try:
        repo, number = analyze_mod.parse_pr_url(item.pr_url)
    except analyze_mod.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def event_stream():
        fields = None
        for evt in analyze_mod.analyze_pr_stream(
            repo,
            number,
            api_key=item.api_key,
            model=item.model,
            base_url=item.base_url,
        ):
            if evt.get("type") == "result":
                fields = evt.get("fields")
                # 落库后把草稿（含 id）作为最终事件推给页面
                draft = drafts_mod.save_draft(
                    fields["title"],
                    fields["category"],
                    fields["content"],
                    tags=fields["tags"],
                    source_pr=fields["source_pr"],
                    content_format=fields["content_format"],
                    knowledge_dir=KNOWLEDGE_DIR,
                )
                yield _sse({"type": "done", "draft": draft})
            else:
                yield _sse(evt)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- 查找 PR：关键词 + 仓库名 → LLM 生成查询、gh 搜索、LLM 筛选排序 ---


@app.post("/api/discover/prs")
def discover_prs(item: PrDiscoverIn) -> dict:
    """一次性查找 PR，返回 {"query","warning","results"}。给 CLI/脚本用。

    api_key 仅用于本次请求转发给 LLM 服务，不落盘、不记录。
    """
    if not item.keyword.strip() and not item.repo.strip():
        raise HTTPException(status_code=400, detail="请至少填写关键词或仓库名")
    try:
        return discover_mod.discover_prs(
            item.keyword,
            item.repo,
            api_key=item.api_key,
            model=item.model,
            base_url=item.base_url,
            limit=item.limit,
            state=item.state,
        )
    except GhError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/discover/prs/stream")
def discover_prs_stream(item: PrDiscoverIn) -> StreamingResponse:
    """流式查找 PR：SSE 逐段推送生成的查询、候选数、模型筛选过程与最终列表。

    事件类型见 discover.discover_prs_stream。api_key 不落盘、不记录。
    """
    if not item.keyword.strip() and not item.repo.strip():
        raise HTTPException(status_code=400, detail="请至少填写关键词或仓库名")

    def event_stream():
        for evt in discover_mod.discover_prs_stream(
            item.keyword,
            item.repo,
            api_key=item.api_key,
            model=item.model,
            base_url=item.base_url,
            limit=item.limit,
            state=item.state,
        ):
            yield _sse(evt)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        content_format=item.content_format,
        knowledge_dir=KNOWLEDGE_DIR,
    )


@app.post("/api/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, item: KnowledgeIn) -> dict:
    """用户 review（可修改）后确认保存：转为正式知识并删除草稿。

    `content_format` 用请求体里的值——那是用户在页面上明确选过的，这里再嗅探一次
    等于推翻用户的选择。
    """
    if not drafts_mod.get_draft(draft_id, KNOWLEDGE_DIR):
        raise HTTPException(status_code=404, detail="草稿不存在")
    path = store.save_knowledge(
        item.title,
        item.category,
        item.content,
        tags=item.tags,
        source_pr=item.source_pr or None,
        content_format=item.content_format,
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
