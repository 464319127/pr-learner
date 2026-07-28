"""llm 传输层测试：用 httpx.MockTransport 覆盖真实的 HTTP 与 SSE 解析路径。

这层原先完全没有测试覆盖：流式行解析、[DONE] 处理、非 200、以及「服务端忽略
stream 标志、整体返回 JSON」的回退分支，都是只在真实网络下才会走到的代码。
"""

import json

import httpx
import pytest

from pr_learner import llm


def _mount(monkeypatch, handler):
    """把 llm 内部的 httpx.Client 换成走 MockTransport 的客户端。"""

    def fake_client(timeout):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm, "_client", fake_client)


def _sse(*chunks: str) -> bytes:
    return "".join(f"data: {c}\n\n" for c in chunks).encode()


# --- call ---


def test_call_sends_system_and_prompt(monkeypatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    _mount(monkeypatch, handler)
    out = llm.call("https://x.example/", "sk-1", "m", "问题", system="你是助手")

    assert out == "ok"
    assert seen["url"] == "https://x.example/v1/messages"
    assert seen["auth"] == "Bearer sk-1"
    assert seen["body"]["system"] == "你是助手"
    assert seen["body"]["messages"] == [{"role": "user", "content": "问题"}]
    assert "stream" not in seen["body"]


def test_call_non_200_raises(monkeypatch) -> None:
    _mount(monkeypatch, lambda req: httpx.Response(429, text="rate limited"))
    with pytest.raises(llm.LlmError, match="429"):
        llm.call("https://x.example", "sk", "m", "p", system="s")


def test_call_transport_error_raises(monkeypatch) -> None:
    def handler(request):
        raise httpx.ConnectError("连不上", request=request)

    _mount(monkeypatch, handler)
    with pytest.raises(llm.LlmError, match="调用 LLM 失败"):
        llm.call("https://x.example", "sk", "m", "p", system="s")


def test_call_openai_shaped_response(monkeypatch) -> None:
    _mount(
        monkeypatch,
        lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]}),
    )
    assert llm.call("https://x.example", "sk", "m", "p", system="s") == "hi"


def test_call_unparseable_response(monkeypatch) -> None:
    _mount(monkeypatch, lambda req: httpx.Response(200, json={"weird": 1}))
    with pytest.raises(llm.LlmError, match="无法从响应解析文本"):
        llm.call("https://x.example", "sk", "m", "p", system="s")


# --- iter_stream ---


def test_iter_stream_parses_anthropic_sse(monkeypatch) -> None:
    body = _sse(
        json.dumps(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "想"}}
        ),
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "答"}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "案"}}),
        "[DONE]",
    )
    _mount(monkeypatch, lambda req: httpx.Response(200, content=body))
    out = list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s"))
    assert out == [("thinking", "想"), ("text", "答"), ("text", "案")]


def test_iter_stream_sets_stream_flag(monkeypatch) -> None:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["accept"] = request.headers["Accept"]
        return httpx.Response(200, content=_sse("[DONE]"))

    _mount(monkeypatch, handler)
    list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s"))
    assert seen["body"]["stream"] is True
    assert seen["accept"] == "text/event-stream"


def test_iter_stream_skips_malformed_data_lines(monkeypatch) -> None:
    body = b'data: {\xe5\x9d\x8f\n\ndata: {"choices":[{"delta":{"content":"good"}}]}\n\n'
    _mount(monkeypatch, lambda req: httpx.Response(200, content=body))
    assert list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s")) == [
        ("text", "good")
    ]


def test_iter_stream_falls_back_to_whole_json(monkeypatch) -> None:
    """服务端忽略 stream 标志、直接返回整个 JSON 时要能兜住。"""
    _mount(
        monkeypatch,
        lambda req: httpx.Response(200, json={"content": [{"type": "text", "text": "一次性"}]}),
    )
    assert list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s")) == [
        ("text", "一次性")
    ]


def test_iter_stream_fallback_unparseable(monkeypatch) -> None:
    _mount(monkeypatch, lambda req: httpx.Response(200, text="就是一段纯文本"))
    with pytest.raises(llm.LlmError, match="无法解析 LLM 流式响应"):
        list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s"))


def test_iter_stream_non_200_raises(monkeypatch) -> None:
    _mount(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(llm.LlmError, match="500"):
        list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s"))


def test_iter_stream_transport_error_raises(monkeypatch) -> None:
    def handler(request):
        raise httpx.ReadTimeout("超时", request=request)

    _mount(monkeypatch, handler)
    with pytest.raises(llm.LlmError, match="调用 LLM 失败"):
        list(llm.iter_stream("https://x.example", "sk", "m", "p", system="s"))


# --- parse_json_object ---


def test_parse_json_object_variants() -> None:
    assert llm.parse_json_object('{"a":1}') == {"a": 1}
    assert llm.parse_json_object('```json\n{"a":2}\n```') == {"a": 2}
    assert llm.parse_json_object('前言 {"a":3} 后记') == {"a": 3}
    with pytest.raises(llm.LlmError):
        llm.parse_json_object("完全不是 JSON")
