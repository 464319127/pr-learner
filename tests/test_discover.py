"""discover 模块测试：查询生成的清洗与降级、候选 join 的防幻觉、流式事件顺序。

不打真实网络：LLM 调用一律 monkeypatch。断言重点是「模型抽风时用户仍能看到东西」
以及「模型不能凭空造出 PR」。
"""

import json

import pytest

from pr_learner import discover, llm
from pr_learner.fetch import GhError, PrSummary


def _pr(number: int, title: str = "t", body: str = "") -> PrSummary:
    return PrSummary(
        repo="o/r",
        number=number,
        title=title,
        url=f"https://github.com/o/r/pull/{number}",
        state="open",
        body=body,
    )


@pytest.fixture
def llm_calls(monkeypatch):
    """记录 llm.call 的调用次数与返回值，便于断言「有没有真的调模型」。"""
    box = {"count": 0, "reply": ""}

    def fake_call(base_url, api_key, model, prompt, *, system, **kw):
        box["count"] += 1
        box["prompt"] = prompt
        box["system"] = system
        if isinstance(box["reply"], Exception):
            raise box["reply"]
        return box["reply"]

    monkeypatch.setattr(llm, "call", fake_call)
    return box


# --- build_query：清洗与降级 ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hicache memory leak", "hicache memory leak"),
        ("```\nmoe performance is:merged\n```", "moe performance is:merged"),
        ("```text\nfoo bar\n```", "foo bar"),
        ('"radix cache eviction"', "radix cache eviction"),
        ("第一行查询\n第二行解释说明", "第一行查询"),
        ("repo:other/x org:acme fix", "fix"),
        ("  \n  ", ""),
    ],
)
def test_clean_query(raw, expected) -> None:
    assert discover._clean_query(raw) == expected


def test_build_query_uses_llm(llm_calls) -> None:
    llm_calls["reply"] = "hicache memory leak"
    query, warning = discover.build_query("hicache 内存泄漏", "o/r", api_key="sk", model="m")
    assert query == "hicache memory leak"
    assert warning is None
    assert llm_calls["count"] == 1


def test_build_query_without_api_key_skips_llm(llm_calls) -> None:
    """没有 key 时必须一次都不调模型——断言调用次数，而非只看结果非空。"""
    query, warning = discover.build_query("关键词", "o/r", api_key="", model="")
    assert query == "关键词"
    assert warning is None
    assert llm_calls["count"] == 0


def test_build_query_llm_failure_warns(llm_calls) -> None:
    """降级回原文时必须给 warning：中文关键词按原文搜大概率返空，
    用户会误以为「没有相关 PR」而不是「翻译失败」。"""
    llm_calls["reply"] = llm.LlmError("502")
    query, warning = discover.build_query("内存泄漏", "o/r", api_key="sk", model="m")
    assert query == "内存泄漏"
    assert warning and "失败" in warning


def test_build_query_empty_output_warns(llm_calls) -> None:
    llm_calls["reply"] = "```\n```"
    query, warning = discover.build_query("内存泄漏", "o/r", api_key="sk", model="m")
    assert query == "内存泄漏"
    assert warning


# --- join_ranked：模型只能重排真实候选 ---


def test_join_ranked_happy_path() -> None:
    candidates = [_pr(1, "第一"), _pr(2, "第二")]
    ranked = discover.join_ranked(
        [{"number": 2, "summary": "更相关", "relevance": "高"}], candidates
    )
    assert len(ranked) == 1
    assert ranked[0]["number"] == 2
    assert ranked[0]["title"] == "第二"
    assert ranked[0]["summary"] == "更相关"
    assert ranked[0]["url"].endswith("/pull/2")


def test_join_ranked_drops_hallucinated_numbers() -> None:
    ranked = discover.join_ranked(
        [{"number": 999, "summary": "编造的"}, {"number": 1, "summary": "真的"}],
        [_pr(1)],
    )
    assert [r["number"] for r in ranked] == [1]


def test_join_ranked_accepts_string_number() -> None:
    """模型高频把 number 输出成字符串。"""
    ranked = discover.join_ranked([{"number": "1", "summary": "s"}], [_pr(1)])
    assert [r["number"] for r in ranked] == [1]


def test_join_ranked_dedupes() -> None:
    ranked = discover.join_ranked(
        [{"number": 1, "summary": "a"}, {"number": 1, "summary": "b"}], [_pr(1)]
    )
    assert len(ranked) == 1
    assert ranked[0]["summary"] == "a"


def test_join_ranked_ignores_junk_items() -> None:
    ranked = discover.join_ranked(
        ["不是对象", {"summary": "缺 number"}, {"number": "abc"}, {"number": 1}], [_pr(1)]
    )
    assert [r["number"] for r in ranked] == [1]


def test_join_ranked_uses_candidate_url_not_model_url() -> None:
    """链接只信 gh 返回的，绝不用模型输出里的。"""
    ranked = discover.join_ranked(
        [{"number": 1, "summary": "s", "url": "https://evil.example/x"}], [_pr(1)]
    )
    assert ranked[0]["url"] == "https://github.com/o/r/pull/1"


def test_join_ranked_keeps_model_order() -> None:
    """排序以数组顺序为准，不看 relevance 字段。"""
    candidates = [_pr(1), _pr(2), _pr(3)]
    ranked = discover.join_ranked(
        [{"number": 3, "relevance": "低"}, {"number": 1, "relevance": "高"}], candidates
    )
    assert [r["number"] for r in ranked] == [3, 1]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("高", "高"),
        ("High", "高"),
        ("很高", "高"),
        ("medium", "中"),
        ("低", "低"),
        ("???", "中"),
        (None, "中"),
    ],
)
def test_normalize_relevance(given, expected) -> None:
    assert discover.normalize_relevance(given) == expected


# --- rank_prs：降级不抛异常 ---


def test_rank_prs_without_api_key_skips_llm(llm_calls) -> None:
    out = discover.rank_prs("kw", [_pr(1)], api_key="", model="")
    assert llm_calls["count"] == 0
    assert [r["number"] for r in out] == [1]
    assert out[0]["summary"] == ""


def test_rank_prs_bad_json_falls_back(llm_calls) -> None:
    llm_calls["reply"] = "这不是 JSON"
    out = discover.rank_prs("kw", [_pr(1), _pr(2)], api_key="sk", model="m")
    assert [r["number"] for r in out] == [1, 2]  # 保持 gh 原始顺序


def test_rank_prs_missing_results_key_falls_back(llm_calls) -> None:
    llm_calls["reply"] = json.dumps({"items": []})
    out = discover.rank_prs("kw", [_pr(1)], api_key="sk", model="m")
    assert [r["number"] for r in out] == [1]


def test_rank_prs_truncates_candidates(llm_calls) -> None:
    """超过上限的候选会被截尾，且截掉的不进提示词。"""
    llm_calls["reply"] = json.dumps({"results": []})
    many = [_pr(i) for i in range(1, discover.MAX_RANK_CANDIDATES + 6)]
    discover.rank_prs("kw", many, api_key="sk", model="m")
    prompt = llm_calls["prompt"]
    assert '"number": 1' in prompt
    assert f'"number": {discover.MAX_RANK_CANDIDATES + 5}' not in prompt


def test_rank_prs_empty_candidates(llm_calls) -> None:
    assert discover.rank_prs("kw", [], api_key="sk", model="m") == []
    assert llm_calls["count"] == 0


# --- discover_prs_stream：事件顺序与降级 ---


def _stream_events(monkeypatch, **kw):
    return list(discover.discover_prs_stream(**kw))


def test_stream_event_order(monkeypatch) -> None:
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [_pr(1), _pr(2)])
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("cache", None))
    monkeypatch.setattr(
        llm,
        "iter_stream",
        lambda *a, **k: iter(
            [("thinking", "看看"), ("text", '{"results":[{"number":2,"summary":"s"}]}')]
        ),
    )
    events = _stream_events(monkeypatch, keyword="缓存", repo="o/r", api_key="sk", model="m")
    kinds = [e["type"] for e in events]

    assert kinds.index("query") < kinds.index("candidates") < kinds.index("result")
    assert "thinking" in kinds
    assert events[-1]["type"] == "result"
    assert [r["number"] for r in events[-1]["results"]] == [2]


def test_stream_no_candidates_is_not_error(monkeypatch) -> None:
    """搜不到东西是正常态，要给空 result 而不是 error。"""
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [])
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("zzz", None))
    events = _stream_events(monkeypatch, keyword="zzz", repo="o/r", api_key="sk", model="m")
    assert [e["type"] for e in events][-1] == "result"
    assert events[-1]["results"] == []
    assert not any(e["type"] == "error" for e in events)


def test_stream_falls_back_to_recent_prs(monkeypatch) -> None:
    """查询没命中时按最近更新兜底列出，避免用户对着空列表发呆。"""
    calls = []

    def fake_search(query, *, repo=None, limit=20, state=None, sort=None):
        calls.append({"query": query, "sort": sort, "state": state})
        return [] if len(calls) == 1 else [_pr(7)]

    monkeypatch.setattr(discover, "search_prs", fake_search)
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("内存泄漏", "翻译失败"))
    events = list(
        discover.discover_prs_stream("内存泄漏", "o/r", api_key="", model="", state="merged")
    )

    assert len(calls) == 2
    assert calls[1]["sort"] == "updated" and calls[1]["query"] == ""
    # 兜底也要守住用户选的状态：选了「仅已合并」却列出 open PR 就是骗人
    assert calls[1]["state"] == "merged"
    assert [r["number"] for r in events[-1]["results"]] == [7]


def test_stream_surfaces_query_warning(monkeypatch) -> None:
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [_pr(1)])
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("原文", "生成搜索语法失败"))
    events = _stream_events(monkeypatch, keyword="原文", repo="o/r", api_key="", model="")
    assert any("生成搜索语法失败" in e.get("message", "") for e in events)


def test_stream_gh_error_becomes_error_event(monkeypatch) -> None:
    def boom(*a, **k):
        raise GhError("仓库不存在或无权访问，请检查仓库名")

    monkeypatch.setattr(discover, "search_prs", boom)
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("fix", None))
    events = _stream_events(monkeypatch, keyword="fix", repo="nope/nope", api_key="", model="")
    assert events[-1]["type"] == "error"
    assert "仓库不存在" in events[-1]["message"]


def test_stream_without_api_key_skips_llm(monkeypatch, llm_calls) -> None:
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [_pr(1)])

    def no_stream(*a, **k):
        raise AssertionError("无 api_key 时不应调用模型")

    monkeypatch.setattr(llm, "iter_stream", no_stream)
    events = _stream_events(monkeypatch, keyword="fix", repo="o/r", api_key="", model="")
    assert llm_calls["count"] == 0
    assert events[-1]["type"] == "result"
    assert [r["number"] for r in events[-1]["results"]] == [1]


def test_stream_llm_failure_degrades_to_search_results(monkeypatch) -> None:
    """模型抽风时降级成未排序结果，用户仍能看到东西。"""
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [_pr(1), _pr(2)])
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("cache", None))
    monkeypatch.setattr(llm, "iter_stream", lambda *a, **k: iter([("text", "非 JSON")]))
    events = _stream_events(monkeypatch, keyword="缓存", repo="o/r", api_key="sk", model="m")
    assert events[-1]["type"] == "result"
    assert [r["number"] for r in events[-1]["results"]] == [1, 2]


def test_stream_all_filtered_out_shows_raw_results(monkeypatch) -> None:
    """模型认为都不相关时也展示原始结果，而不是让页面空白。"""
    monkeypatch.setattr(discover, "search_prs", lambda *a, **k: [_pr(1)])
    monkeypatch.setattr(discover, "build_query", lambda *a, **k: ("cache", None))
    monkeypatch.setattr(llm, "iter_stream", lambda *a, **k: iter([("text", '{"results":[]}')]))
    events = _stream_events(monkeypatch, keyword="缓存", repo="o/r", api_key="sk", model="m")
    assert [r["number"] for r in events[-1]["results"]] == [1]


def test_stream_requires_keyword_or_repo() -> None:
    events = list(discover.discover_prs_stream("", "", api_key="", model=""))
    assert events == [{"type": "error", "message": "请至少填写关键词或仓库名"}]
