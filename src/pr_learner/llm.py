"""LLM 传输层：调用 messages 接口，解析响应与流式增量。

本模块**不认识任何业务**——不知道什么是 PR、什么是知识草稿，只负责把
(system, prompt) 发出去、把文本/增量拿回来。业务提示词由调用方组装
（见 prompts.py 与 analyze.py / discover.py）。

接口形如：
    POST {base_url}/v1/messages
    Authorization: Bearer <api_key>
    {"model": "...", "max_tokens": N, "system": "...", "messages": [...]}
同时兼容 Anthropic 与 OpenAI 两种响应/流式格式。

`system` 是必填关键字参数：默认值填别人的 system prompt 会导致模型返回
结构完全不对但仍是合法 JSON 的结果，静默产出空列表且测试抓不到。
"""

from __future__ import annotations

import json
import re

import httpx

DEFAULT_BASE_URL = "https://oneapi-comate.baidu-int.com"

# 一次性调用与流式调用的默认超时。小任务（如生成搜索语法）应显式传更短的值，
# 否则一次网络抽风会把交互式页面卡住几分钟。
DEFAULT_TIMEOUT = 120.0
DEFAULT_STREAM_TIMEOUT = 300.0


class LlmError(RuntimeError):
    """调用 LLM 或解析其输出时出错。"""


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/v1/messages"


def _client(timeout: float) -> httpx.Client:
    """构造 HTTP 客户端。独立成函数是为了让测试能换成 MockTransport。"""
    return httpx.Client(timeout=timeout)


def _headers(api_key: str, *, stream: bool = False) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers


def _payload(model: str, prompt: str, system: str, max_tokens: int, *, stream: bool) -> dict:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if stream:
        payload["stream"] = True
    return payload


def call(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    system: str,
    max_tokens: int = 4096,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """一次性调用 messages 接口，返回模型输出的文本。"""
    try:
        with _client(timeout) as client:
            resp = client.post(
                _endpoint(base_url),
                headers=_headers(api_key),
                json=_payload(model, prompt, system, max_tokens, stream=False),
            )
    except httpx.HTTPError as exc:
        raise LlmError(f"调用 LLM 失败: {exc}") from exc

    if resp.status_code != 200:
        raise LlmError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")

    return _extract_text(resp.json())


def _extract_text(data: dict) -> str:
    """从 Anthropic / OpenAI 兼容响应里取出文本。"""
    # Anthropic: {"content": [{"type":"text","text":"..."}]}
    content = data.get("content")
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        if any(parts):
            return "".join(parts)
    # OpenAI: {"choices":[{"message":{"content":"..."}}]}
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {})
        if isinstance(msg.get("content"), str):
            return msg["content"]
    raise LlmError(f"无法从响应解析文本: {json.dumps(data)[:300]}")


def parse_json_object(text: str) -> dict:
    """从模型输出里抽出 JSON 对象，容忍 markdown 围栏或前后噪声。"""
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        # 退而求其次，截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError(f"模型输出不是合法 JSON: {exc}") from exc


def _extract_stream_delta(evt: dict):
    """从一个流式 SSE 事件里取出增量，yield (kind, text)，kind ∈ {'thinking','text'}。

    同时兼容 Anthropic（content_block_delta）与 OpenAI（choices[].delta）两种流式格式。
    """
    # Anthropic: {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}
    if evt.get("type") == "content_block_delta":
        d = evt.get("delta", {}) or {}
        if d.get("type") == "text_delta" and d.get("text"):
            yield ("text", d["text"])
        elif d.get("type") == "thinking_delta" and d.get("thinking"):
            yield ("thinking", d["thinking"])
        return
    # OpenAI: {"choices":[{"delta":{"content":"...","reasoning_content":"..."}}]}
    choices = evt.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {}) or {}
        # 部分兼容接口把思考链放在 reasoning_content / reasoning 字段
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            yield ("thinking", reasoning)
        if delta.get("content"):
            yield ("text", delta["content"])


def iter_stream(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    system: str,
    max_tokens: int = 4096,
    timeout: float = DEFAULT_STREAM_TIMEOUT,
):
    """流式调用 messages 接口，逐段 yield (kind, text)。

    若服务端未按 SSE 流式返回（忽略了 stream 标志），退回整体解析一次性产出文本。
    """
    try:
        with (
            _client(timeout) as client,
            client.stream(
                "POST",
                _endpoint(base_url),
                headers=_headers(api_key, stream=True),
                json=_payload(model, prompt, system, max_tokens, stream=True),
            ) as resp,
        ):
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")
                raise LlmError(f"LLM 返回 {resp.status_code}: {body[:300]}")
            saw_sse = False
            raw_lines: list[str] = []
            for line in resp.iter_lines():
                if line is None:
                    continue
                raw_lines.append(line)
                s = line.strip()
                if not s.startswith("data:"):
                    continue
                saw_sse = True
                data = s[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                yield from _extract_stream_delta(evt)
            if not saw_sse:
                # 服务端未流式返回，退回整体解析
                body = "\n".join(raw_lines).strip()
                try:
                    text = _extract_text(json.loads(body))
                except (json.JSONDecodeError, LlmError) as exc:
                    raise LlmError(f"无法解析 LLM 流式响应: {body[:300]}") from exc
                if text:
                    yield ("text", text)
    except httpx.HTTPError as exc:
        raise LlmError(f"调用 LLM 失败: {exc}") from exc
