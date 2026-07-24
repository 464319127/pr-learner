"""API 层测试：查询与提交知识，使用 FastAPI TestClient + 临时知识库。"""

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path) -> TestClient:
    """用临时知识库目录构造一个隔离的 app。"""
    import pr_learner.api as api

    # 通过环境变量隔离知识库目录，重载模块使其生效
    import os

    os.environ["PR_LEARNER_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    importlib.reload(api)
    return TestClient(api.app)


def test_create_search_and_categories(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # 初始为空
    assert client.get("/api/categories").json() == {}
    assert client.get("/api/search").json() == []

    # 提交一条知识
    payload = {
        "title": "循环重试要加抖动",
        "category": "可靠性",
        "content": "指数退避叠加随机抖动，避免惊群。",
        "tags": ["重试", "退避"],
        "source_pr": "owner/repo#7",
    }
    res = client.post("/api/knowledge", json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True

    # 分类统计更新
    assert client.get("/api/categories").json() == {"可靠性": 1}

    # 关键词检索命中
    hits = client.get("/api/search", params={"keyword": "抖动"}).json()
    assert len(hits) == 1
    assert hits[0]["title"] == "循环重试要加抖动"
    assert hits[0]["source_pr"] == "owner/repo#7"

    # 标签检索命中
    assert client.get("/api/search", params={"tag": "退避"}).json()
    # 分类过滤
    assert client.get("/api/search", params={"category": "可靠性"}).json()
    # 不匹配
    assert client.get("/api/search", params={"keyword": "不存在xyz"}).json() == []


def test_create_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # 缺 content，pydantic 应拒绝
    res = client.post("/api/knowledge", json={"title": "x", "category": "y"})
    assert res.status_code == 422


def test_analyze_endpoint_creates_draft(tmp_path: Path, monkeypatch) -> None:
    """POST /api/analyze：替身跳过 gh+LLM，验证会落一条草稿。"""
    client = _client(tmp_path)
    import pr_learner.api as api

    monkeypatch.setattr(
        api.analyze_mod,
        "analyze_pr",
        lambda repo, number, **kw: {
            "title": "分析出的标题",
            "category": "网络",
            "content": "正文",
            "tags": ["tcp"],
            "source_pr": f"{repo}#{number}",
        },
    )
    res = client.post(
        "/api/analyze",
        json={
            "pr_url": "https://github.com/o/r/pull/7",
            "api_key": "sk-x",
            "model": "gpt-5.6-sol",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["source_pr"] == "o/r#7"
    # 草稿已生成，尚未进正式库
    assert len(client.get("/api/drafts").json()) == 1
    assert client.get("/api/categories").json() == {}


def test_analyze_endpoint_bad_url(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post(
        "/api/analyze",
        json={"pr_url": "garbage", "api_key": "sk-x", "model": "m"},
    )
    assert res.status_code == 400


def test_analyze_stream_endpoint(tmp_path: Path, monkeypatch) -> None:
    """POST /api/analyze/stream：SSE 推送过程事件，结束落库草稿。"""
    client = _client(tmp_path)
    import pr_learner.api as api

    def fake_stream(repo, number, **kw):
        yield {"type": "status", "message": "正在拉取 PR …"}
        yield {"type": "thinking", "text": "分析 diff"}
        yield {"type": "text", "text": "正文片段"}
        yield {
            "type": "result",
            "fields": {
                "title": "流式标题",
                "category": "网络",
                "content": "正文",
                "tags": ["tcp"],
                "source_pr": f"{repo}#{number}",
            },
        }

    monkeypatch.setattr(api.analyze_mod, "analyze_pr_stream", fake_stream)
    res = client.post(
        "/api/analyze/stream",
        json={"pr_url": "https://github.com/o/r/pull/7", "api_key": "sk-x", "model": "m"},
    )
    assert res.status_code == 200
    body = res.text
    assert "data:" in body
    assert "thinking" in body
    assert "done" in body  # result 被转成 done 事件（含落库草稿）
    # 草稿已落库
    assert len(client.get("/api/drafts").json()) == 1


def test_draft_review_and_approve_flow(tmp_path: Path) -> None:
    """agent 写草稿 → 列出 → 用户修改后 approve → 转正入库、草稿删除。"""
    client = _client(tmp_path)

    # agent 写入草稿
    draft_payload = {
        "title": "agent 生成的标题",
        "category": "网络",
        "content": "agent 分析出的正文。",
        "tags": ["tcp"],
        "source_pr": "owner/repo#9",
    }
    res = client.post("/api/drafts", json=draft_payload)
    assert res.status_code == 200, res.text
    draft_id = res.json()["id"]

    # 草稿可列出，但还没进正式知识库
    assert len(client.get("/api/drafts").json()) == 1
    assert client.get("/api/categories").json() == {}

    # 用户 review 时改了标题和标签，再 approve
    approved = {
        "title": "用户改过的标题",
        "category": "网络",
        "content": "agent 分析出的正文。",
        "tags": ["tcp", "拥塞控制"],
        "source_pr": "owner/repo#9",
    }
    res = client.post(f"/api/drafts/{draft_id}/approve", json=approved)
    assert res.status_code == 200, res.text

    # 草稿已消费，正式库出现修改后的内容
    assert client.get("/api/drafts").json() == []
    assert client.get("/api/categories").json() == {"网络": 1}
    hits = client.get("/api/search", params={"keyword": "用户改过"}).json()
    assert len(hits) == 1
    assert "拥塞控制" in hits[0]["tags"]


def test_discard_draft(tmp_path: Path) -> None:
    client = _client(tmp_path)
    draft_id = client.post(
        "/api/drafts",
        json={"title": "t", "category": "c", "content": "x"},
    ).json()["id"]
    assert client.delete(f"/api/drafts/{draft_id}").status_code == 200
    assert client.get("/api/drafts").json() == []
    # 再删不存在的返回 404
    assert client.delete(f"/api/drafts/{draft_id}").status_code == 404


def test_index_page_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 200
    assert "pr-learner" in res.text
    assert "vue" in res.text.lower()
