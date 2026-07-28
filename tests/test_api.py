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


# --- 查找 PR ---


def test_discover_prs_endpoint(tmp_path: Path, monkeypatch) -> None:
    """POST /api/discover/prs：替身跳过 gh+LLM，验证返回 query 与结果列表。"""
    client = _client(tmp_path)
    from pr_learner import api

    monkeypatch.setattr(
        api.discover_mod,
        "discover_prs",
        lambda keyword, repo, **kw: {
            "query": "cache is:merged",
            "warning": "",
            "results": [{"repo": repo, "number": 1, "title": "t", "url": "u", "summary": "s"}],
        },
    )
    res = client.post(
        "/api/discover/prs",
        json={"keyword": "缓存", "repo": "o/r", "api_key": "sk-x", "model": "m"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["query"] == "cache is:merged"
    assert body["results"][0]["number"] == 1
    # api_key 只做本次转发，绝不回显
    assert "sk-x" not in res.text


def test_discover_prs_requires_keyword_or_repo(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post("/api/discover/prs", json={"keyword": "  ", "repo": ""})
    assert res.status_code == 400


def test_discover_prs_gh_error_is_400(tmp_path: Path, monkeypatch) -> None:
    """gh 失败（仓库不存在/未登录）是用户侧问题，给 400 而不是 500。"""
    client = _client(tmp_path)
    from pr_learner import api
    from pr_learner.fetch import GhError

    def boom(keyword, repo, **kw):
        raise GhError("仓库不存在或无权访问，请检查仓库名")

    monkeypatch.setattr(api.discover_mod, "discover_prs", boom)
    res = client.post("/api/discover/prs", json={"keyword": "x", "repo": "nope/nope"})
    assert res.status_code == 400
    assert "仓库不存在" in res.json()["detail"]


def test_discover_prs_llm_error_is_502(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    from pr_learner import api
    from pr_learner.llm import LlmError

    def boom(keyword, repo, **kw):
        raise LlmError("LLM 返回 401")

    monkeypatch.setattr(api.discover_mod, "discover_prs", boom)
    res = client.post(
        "/api/discover/prs",
        json={"keyword": "x", "repo": "o/r", "api_key": "sk", "model": "m"},
    )
    assert res.status_code == 502


def test_discover_prs_stream_endpoint(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    from pr_learner import api

    def fake_stream(keyword, repo, **kw):
        yield {"type": "status", "message": "正在搜索 …"}
        yield {"type": "query", "query": "cache"}
        yield {"type": "candidates", "count": 2}
        yield {"type": "thinking", "text": "筛选"}
        yield {"type": "result", "results": [{"number": 1, "url": "u", "title": "t"}]}

    monkeypatch.setattr(api.discover_mod, "discover_prs_stream", fake_stream)
    res = client.post(
        "/api/discover/prs/stream",
        json={"keyword": "缓存", "repo": "o/r", "api_key": "sk-x", "model": "m"},
    )
    assert res.status_code == 200
    body = res.text
    for token in ("data:", "query", "candidates", "thinking", "result"):
        assert token in body
    assert "sk-x" not in body


def test_discover_prs_stream_requires_input(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post("/api/discover/prs/stream", json={"keyword": "", "repo": ""})
    assert res.status_code == 400


def test_stream_event_types_handled_by_frontend(monkeypatch) -> None:
    """SSE 是纯字符串协议：后端新增/改名事件类型而前端没跟上会静默失效。

    这里把两个流式生成器能产出的 type 全部收集出来，逐个检查页面 JS 里有处理。
    """
    from pr_learner import analyze, api, discover, llm
    from pr_learner.fetch import PrSummary, PullRequest

    html = (Path(api.__file__).parent / "static" / "index.html").read_text("utf-8")

    fake_pr = PullRequest(
        repo="o/r", number=1, title="t", author="a", state="OPEN", url="u", body="b", diff="d"
    )
    monkeypatch.setattr(analyze, "fetch_pr", lambda repo, number: fake_pr)
    monkeypatch.setattr(
        analyze,
        "_iter_llm_stream",
        lambda *a, **k: iter([("thinking", "t"), ("text", '{"title":"x","content":"y"}')]),
    )
    analyze_types = {
        e["type"] for e in analyze.analyze_pr_stream("o/r", 1, api_key="sk", model="m")
    }

    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("q", "翻译失败"))
    monkeypatch.setattr(
        discover,
        "search_prs",
        lambda *a, **k: [PrSummary(repo="o/r", number=1, title="t", url="u")],
    )
    monkeypatch.setattr(
        llm, "iter_stream", lambda *a, **k: iter([("thinking", "t"), ("text", '{"results":[]}')])
    )
    discover_types = {
        e["type"] for e in discover.discover_prs_stream("kw", "o/r", api_key="sk", model="m")
    }

    # analyze 的 result 事件由 api.py 转成 done 后才推给页面
    for t in (analyze_types - {"result"}) | {"done"}:
        assert f"'{t}'" in html, f"前端 handleAnalyzeEvent 未处理事件类型 {t}"
    for t in discover_types | {"error"}:
        assert f"'{t}'" in html, f"前端 handleDiscoverEvent 未处理事件类型 {t}"


def test_frontend_sends_all_required_analyze_fields() -> None:
    """AnalyzeIn 的必填字段都要出现在页面组装的请求体里。

    凭证被抽到共享的 this.llm 后，页面若还写 JSON.stringify(this.analyze) 就会
    缺 api_key/model 被 422 挡下——而后端测试直接构造 json，永远抓不到这个。
    """
    import re

    from pr_learner import api

    html = (Path(api.__file__).parent / "static" / "index.html").read_text("utf-8")
    m = re.search(r"'/api/analyze/stream'.*?body:\s*JSON\.stringify\((.*?)\),", html, re.DOTALL)
    assert m, "找不到 /api/analyze/stream 的请求体组装代码"
    body_expr = m.group(1)

    required = {n for n, f in api.AnalyzeIn.model_fields.items() if f.is_required()}
    assert required, "AnalyzeIn 应有必填字段"
    for name in required:
        # 字段要么显式出现，要么由 ...this.llm 展开带进来
        assert name in body_expr or "...this.llm" in body_expr, (
            f"请求体缺少必填字段 {name}：{body_expr}"
        )
    assert "...this.llm" in body_expr, "凭证应来自共享的 this.llm"
