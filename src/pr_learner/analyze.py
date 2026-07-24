"""调用 LLM 分析 PR，生成知识草稿。

用户在页面填写 base_url / api_key / model，服务端用 gh 拉取 PR 后，
把 PR 内容发给 LLM（Anthropic messages 兼容接口），让它产出结构化的
标题/分类/标签/正文，落成一条待审草稿。

接口形如：
    POST {base_url}/v1/messages
    Authorization: Bearer <api_key>
    {"model": "...", "max_tokens": N, "messages": [{"role":"user","content":"..."}]}
响应为 Anthropic 格式：{"content": [{"type":"text","text": "..."}]}

提示词维护在 prompt_templates/ 目录下的纯文本文件里，用户和 agent 可直接编辑：
    system_prompt.md     —— system 提示词，用 {output_template} 占位输出结构
    user_prompt.md       —— user 提示词，用 {pr_markdown} 占位 PR 内容
    output_template.json —— 期望的 JSON 输出结构（字段名须与本模块解析一致）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from pr_learner.fetch import fetch_pr, to_markdown

DEFAULT_BASE_URL = "https://oneapi-comate.baidu-int.com"

# 提示词模板目录：随包分发，用户可直接编辑其中的文本文件调整提示词。
PROMPT_TEMPLATES_DIR = Path(__file__).parent / "prompt_templates"


def _read_template(name: str) -> str:
    """读取 prompt_templates/ 下的模板文件内容（去掉首尾空白）。"""
    return (PROMPT_TEMPLATES_DIR / name).read_text(encoding="utf-8").strip()


def build_system_prompt() -> str:
    """组装 system 提示词：把输出模板嵌入 system_prompt.md 的 {output_template} 占位。"""
    template = _read_template("system_prompt.md")
    output_template = _read_template("output_template.json")
    return template.replace("{output_template}", output_template)


def build_user_prompt(pr_markdown: str) -> str:
    """组装 user 提示词：把 PR Markdown 填入 user_prompt.md 的 {pr_markdown} 占位。"""
    template = _read_template("user_prompt.md")
    return template.replace("{pr_markdown}", pr_markdown)


class AnalyzeError(RuntimeError):
    """分析过程出错。"""


def _call_llm(base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = 4096) -> str:
    """调用 messages 接口，返回模型输出的文本。"""
    url = base_url.rstrip("/") + "/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": build_system_prompt(),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = httpx.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json=payload,
            timeout=120.0,
        )
    except httpx.HTTPError as exc:
        raise AnalyzeError(f"调用 LLM 失败: {exc}") from exc

    if resp.status_code != 200:
        raise AnalyzeError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    return _extract_text(data)


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
    raise AnalyzeError(f"无法从响应解析文本: {json.dumps(data)[:300]}")


def _parse_json_object(text: str) -> dict:
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
        raise AnalyzeError(f"模型输出不是合法 JSON: {exc}") from exc


def _fields_from_obj(obj: dict, pr, repo: str, number: int) -> dict:
    """把模型输出的 JSON 对象规整成草稿字段（title/category/tags/content/source_pr）。"""
    tags = obj.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "title": str(obj.get("title", "")).strip() or pr.title,
        "category": str(obj.get("category", "")).strip() or "未分类",
        "content": str(obj.get("content", "")).strip(),
        "tags": [str(t).strip() for t in tags if str(t).strip()],
        "source_pr": f"{repo}#{number}",
    }


def analyze_pr(
    repo: str,
    number: int,
    *,
    api_key: str,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """拉取并分析一个 PR，返回草稿字段 dict（title/category/tags/content/source_pr）。"""
    pr = fetch_pr(repo, number)
    pr_md = to_markdown(pr)
    prompt = build_user_prompt(pr_md)

    raw = _call_llm(base_url, api_key, model, prompt)
    obj = _parse_json_object(raw)
    return _fields_from_obj(obj, pr, repo, number)


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


def _iter_llm_stream(
    base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = 4096
):
    """流式调用 messages 接口，逐段 yield (kind, text)。

    若服务端未按 SSE 流式返回（忽略了 stream 标志），退回整体解析一次性产出文本。
    """
    url = base_url.rstrip("/") + "/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": build_system_prompt(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
    }
    try:
        with httpx.stream(
            "POST", url, headers=headers, json=payload, timeout=300.0
        ) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")
                raise AnalyzeError(f"LLM 返回 {resp.status_code}: {body[:300]}")
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
                except (json.JSONDecodeError, AnalyzeError) as exc:
                    raise AnalyzeError(
                        f"无法解析 LLM 流式响应: {body[:300]}"
                    ) from exc
                if text:
                    yield ("text", text)
    except httpx.HTTPError as exc:
        raise AnalyzeError(f"调用 LLM 失败: {exc}") from exc


def analyze_pr_stream(
    repo: str,
    number: int,
    *,
    api_key: str,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
):
    """流式分析一个 PR，逐个 yield 事件 dict，供服务端转成 SSE 推给页面。

    事件类型：
    - {"type":"status","message":...}  阶段提示（拉取 PR、开始分析等）
    - {"type":"thinking","text":...}   模型思考过程增量
    - {"type":"text","text":...}       模型输出正文增量
    - {"type":"result","fields":{...}} 解析完成的草稿字段（尚未落库）
    - {"type":"error","message":...}   出错
    """
    yield {"type": "status", "message": f"正在拉取 PR {repo}#{number} …"}
    try:
        pr = fetch_pr(repo, number)
    except Exception as exc:  # noqa: BLE001 - 统一转成流式错误事件反馈给页面
        yield {"type": "error", "message": f"拉取 PR 失败: {exc}"}
        return

    pr_md = to_markdown(pr)
    prompt = build_user_prompt(pr_md)
    yield {"type": "status", "message": "PR 已拉取，正在调用模型分析 …"}

    chunks: list[str] = []
    try:
        for kind, text in _iter_llm_stream(base_url, api_key, model, prompt):
            if kind == "text":
                chunks.append(text)
            yield {"type": kind, "text": text}
    except AnalyzeError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {"type": "status", "message": "模型输出完成，正在整理草稿 …"}
    try:
        obj = _parse_json_object("".join(chunks))
    except AnalyzeError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {"type": "result", "fields": _fields_from_obj(obj, pr, repo, number)}


_PR_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")


def parse_pr_url(url: str) -> tuple[str, int]:
    """把 PR 链接解析成 (repo, number)。也接受 'owner/repo#123' 形式。"""
    m = _PR_URL_RE.search(url)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"\s*([^/\s]+/[^/#\s]+)#(\d+)\s*$", url)
    if m:
        return m.group(1), int(m.group(2))
    raise AnalyzeError(f"无法识别的 PR 地址: {url}")
