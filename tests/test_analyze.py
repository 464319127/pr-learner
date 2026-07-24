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


def test_build_system_prompt_embeds_output_template() -> None:
    sp = analyze.build_system_prompt()
    # 输出模板的字段应被嵌入 system 提示词，且占位符已被替换
    assert "{output_template}" not in sp
    assert '"title"' in sp and '"category"' in sp and '"tags"' in sp


def test_build_user_prompt_injects_pr_markdown() -> None:
    up = analyze.build_user_prompt("# PR 内容\ndiff...")
    assert "{pr_markdown}" not in up
    assert "# PR 内容" in up


def test_extract_stream_delta_anthropic() -> None:
    evt = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "abc"}}
    assert list(analyze._extract_stream_delta(evt)) == [("text", "abc")]
    evt = {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "想"}}
    assert list(analyze._extract_stream_delta(evt)) == [("thinking", "想")]


def test_extract_stream_delta_openai() -> None:
    evt = {"choices": [{"delta": {"content": "hi", "reasoning_content": "推理"}}]}
    out = list(analyze._extract_stream_delta(evt))
    assert ("thinking", "推理") in out
    assert ("text", "hi") in out


def test_analyze_pr_stream_emits_status_and_result(monkeypatch) -> None:
    """流式分析应先出 status，再逐段 text，最后给出解析好的 result。"""
    from pr_learner.fetch import PullRequest

    fake_pr = PullRequest(
        repo="o/r", number=5, title="兜底标题", author="a", state="MERGED",
        url="u", body="b", diff="d",
    )
    monkeypatch.setattr(analyze, "fetch_pr", lambda repo, number: fake_pr)
    # 模型分两段吐出一个完整 JSON
    monkeypatch.setattr(
        analyze,
        "_iter_llm_stream",
        lambda *a, **k: iter([
            ("thinking", "先看 diff"),
            ("text", '{"title":"标题","category":"网络",'),
            ("text", '"tags":["tcp"],"content":"正文"}'),
        ]),
    )
    events = list(analyze.analyze_pr_stream("o/r", 5, api_key="sk", model="m"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status"
    assert "thinking" in kinds and "text" in kinds
    assert kinds[-1] == "result"
    fields = events[-1]["fields"]
    assert fields["title"] == "标题"
    assert fields["tags"] == ["tcp"]
    assert fields["source_pr"] == "o/r#5"


def test_analyze_pr_stream_reports_error_on_bad_json(monkeypatch) -> None:
    from pr_learner.fetch import PullRequest

    fake_pr = PullRequest(
        repo="o/r", number=5, title="t", author="a", state="OPEN",
        url="u", body="b", diff="d",
    )
    monkeypatch.setattr(analyze, "fetch_pr", lambda repo, number: fake_pr)
    monkeypatch.setattr(
        analyze, "_iter_llm_stream", lambda *a, **k: iter([("text", "这不是JSON")])
    )
    events = list(analyze.analyze_pr_stream("o/r", 5, api_key="sk", model="m"))
    assert events[-1]["type"] == "error"


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
