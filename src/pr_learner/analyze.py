"""调用 LLM 分析 PR，生成知识草稿。

用户在页面填写 base_url / api_key / model，服务端用 gh 拉取 PR 后，
把 PR 内容发给 LLM（Anthropic messages 兼容接口），让它产出结构化的
标题/分类/标签/正文，落成一条待审草稿。

接口形如：
    POST {base_url}/v1/messages
    Authorization: Bearer <api_key>
    {"model": "...", "max_tokens": N, "messages": [{"role":"user","content":"..."}]}
响应为 Anthropic 格式：{"content": [{"type":"text","text": "..."}]}
"""

from __future__ import annotations

import json
import re

import httpx

from pr_learner.fetch import fetch_pr, to_markdown

DEFAULT_BASE_URL = "https://oneapi-comate.baidu-int.com"

_SYSTEM_PROMPT = """你是资深工程师，负责阅读 GitHub PR 并沉淀可复用的知识。
阅读给定的 PR（描述、diff、评论），提炼出对其他工程师有价值的知识点：\
根因、设计权衡、易踩的坑、可迁移的教训，而不是流水账式复述改动。

只返回一个 JSON 对象，不要任何额外解释或 markdown 代码围栏，字段如下：
{
  "title": "一句话概括这条知识（不是 PR 标题的照搬）",
  "category": "简短分类，如 CUDA与量化 / 并发 / 网络 / 构建工程 等",
  "tags": ["3-6 个小写标签"],
  "content": "Markdown 正文，分小节讲清根因、机理、修复、可迁移教训"
}
正文用中文。"""


class AnalyzeError(RuntimeError):
    """分析过程出错。"""


def _call_llm(base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = 4096) -> str:
    """调用 messages 接口，返回模型输出的文本。"""
    url = base_url.rstrip("/") + "/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": _SYSTEM_PROMPT,
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
    prompt = f"请阅读以下 PR 并按要求输出知识 JSON：\n\n{pr_md}"

    raw = _call_llm(base_url, api_key, model, prompt)
    obj = _parse_json_object(raw)

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
