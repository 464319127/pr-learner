"""analyze 模块测试：PR 地址解析、响应文本抽取、JSON 解析。

不打真实网络，_call_llm 通过 monkeypatch 替身；只验证纯逻辑与拼装。
"""

import pytest

from pr_learner import analyze


def test_parse_pr_url_variants() -> None:
    assert analyze.parse_pr_url("https://github.com/sgl-project/sglang/pull/32188") == (
        "sgl-project/sglang",
        32188,
    )
    assert analyze.parse_pr_url("owner/repo#123") == ("owner/repo", 123)
    with pytest.raises(analyze.AnalyzeError):
        analyze.parse_pr_url("not-a-pr")


def test_extract_text_anthropic() -> None:
    data = {"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]}
    assert analyze._extract_text(data) == "hello world"


def test_extract_text_openai() -> None:
    data = {"choices": [{"message": {"content": "hi"}}]}
    assert analyze._extract_text(data) == "hi"


def test_parse_json_object_with_fence() -> None:
    text = '解释一下：\n```json\n{"title": "t", "tags": ["a"]}\n```\n完毕'
    obj = analyze._parse_json_object(text)
    assert obj["title"] == "t"
    assert obj["tags"] == ["a"]


def test_parse_json_object_bare() -> None:
    obj = analyze._parse_json_object('{"title": "x"}')
    assert obj["title"] == "x"


def test_analyze_pr_assembles_fields(monkeypatch) -> None:
    # 替身：跳过 gh 拉取和真实 LLM 调用
    from pr_learner.fetch import PullRequest

    fake_pr = PullRequest(
        repo="o/r", number=5, title="原始PR标题", author="a", state="MERGED",
        url="u", body="b", diff="d",
    )
    monkeypatch.setattr(analyze, "fetch_pr", lambda repo, number: fake_pr)
    monkeypatch.setattr(
        analyze,
        "_call_llm",
        lambda *a, **k: '{"title":"提炼标题","category":"并发","tags":["锁","死锁"],"content":"正文"}',
    )

    out = analyze.analyze_pr("o/r", 5, api_key="sk-x", model="m")
    assert out["title"] == "提炼标题"
    assert out["category"] == "并发"
    assert out["tags"] == ["锁", "死锁"]
    assert out["content"] == "正文"
    assert out["source_pr"] == "o/r#5"


def test_analyze_pr_falls_back_to_pr_title(monkeypatch) -> None:
    from pr_learner.fetch import PullRequest

    fake_pr = PullRequest(
        repo="o/r", number=5, title="兜底标题", author="a", state="OPEN",
        url="u", body="b", diff="d",
    )
    monkeypatch.setattr(analyze, "fetch_pr", lambda repo, number: fake_pr)
    # 模型没给 title，应回退到 PR 标题
    monkeypatch.setattr(analyze, "_call_llm", lambda *a, **k: '{"content":"正文"}')
    out = analyze.analyze_pr("o/r", 5, api_key="sk-x", model="m")
    assert out["title"] == "兜底标题"
    assert out["category"] == "未分类"
