"""调用 LLM 分析 PR，生成知识草稿。

用户在页面填写 base_url / api_key / model，服务端用 gh 拉取 PR 后，
把 PR 内容发给 LLM，让它产出结构化的标题/分类/标签/正文，落成一条待审草稿。

LLM 传输与响应解析在 `llm.py`，提示词模板读取在 `prompts.py`；本模块只负责
「PR → 知识草稿」这段业务：组装提示词、解析字段、串起流式事件。

提示词维护在 prompt_templates/analyze/ 下的纯文本文件里，用户和 agent 可直接编辑：
    system.md     —— system 提示词，用 {output_template} 占位输出结构
    user.md       —— user 提示词，用 {pr_markdown} 占位 PR 内容
    output.json   —— 期望的 JSON 输出结构（字段名须与本模块解析一致）
"""

from __future__ import annotations

import re

from pr_learner import llm, prompts
from pr_learner.fetch import fetch_pr, to_markdown

# 以下别名保持向后兼容：api.py 依赖 analyze_mod.AnalyzeError / DEFAULT_BASE_URL，
# 测试直接调用 analyze._extract_text / _parse_json_object / _extract_stream_delta。
AnalyzeError = llm.LlmError
DEFAULT_BASE_URL = llm.DEFAULT_BASE_URL
_extract_text = llm._extract_text
_extract_stream_delta = llm._extract_stream_delta
_parse_json_object = llm.parse_json_object


def build_system_prompt() -> str:
    """组装 system 提示词：把输出模板嵌入 analyze/system.md 的 {output_template} 占位。"""
    return prompts.render(
        "analyze/system.md", output_template=prompts.read("analyze/output.json")
    )


def build_user_prompt(pr_markdown: str) -> str:
    """组装 user 提示词：把 PR Markdown 填入 analyze/user.md 的 {pr_markdown} 占位。"""
    return prompts.render("analyze/user.md", pr_markdown=pr_markdown)


def _call_llm(
    base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = 4096
) -> str:
    """调用 messages 接口，返回模型输出的文本。"""
    return llm.call(
        base_url,
        api_key,
        model,
        prompt,
        system=build_system_prompt(),
        max_tokens=max_tokens,
    )


def _iter_llm_stream(
    base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = 4096
):
    """流式调用 messages 接口，逐段 yield (kind, text)。"""
    yield from llm.iter_stream(
        base_url,
        api_key,
        model,
        prompt,
        system=build_system_prompt(),
        max_tokens=max_tokens,
    )


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
