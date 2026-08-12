"""调用 LLM 分析 PR，生成知识草稿。

用户在页面填写 base_url / api_key / model，服务端用 gh 拉取 PR 后，
把 PR 内容发给 LLM，让它产出结构化的标题/分类/标签/正文，落成一条待审草稿。

LLM 传输与响应解析在 `llm.py`，提示词模板读取在 `prompts.py`；本模块只负责
「PR → 知识草稿」这段业务：组装提示词、解析字段、串起流式事件。

提示词维护在 prompt_templates/analyze/ 下的纯文本文件里，用户和 agent 可直接编辑：
    system.md      —— system 提示词，用 {output_template} 与 {html_rules} 两个占位
    user.md        —— user 提示词，用 {pr_markdown} 占位 PR 内容
    output.json    —— 期望的 JSON 输出结构（字段名须与本模块解析一致）
    html_rules.md  —— 正文的 HTML 标签/class 白名单与写法要求（要与前端消毒白名单对齐）
"""

from __future__ import annotations

import os
import re

from pr_learner import htmltext, llm, prompts, store
from pr_learner.fetch import fetch_pr, to_markdown

# 以下别名保持向后兼容：api.py 依赖 analyze_mod.AnalyzeError / DEFAULT_BASE_URL，
# 测试直接调用 analyze._extract_text / _parse_json_object / _extract_stream_delta。
AnalyzeError = llm.LlmError
DEFAULT_BASE_URL = llm.DEFAULT_BASE_URL
_extract_text = llm._extract_text
_extract_stream_delta = llm._extract_stream_delta
_parse_json_object = llm.parse_json_object

# 它管的是**整个 JSON 输出**（title/category/tags + 正文 + JSON 结构），不只是正文；
# 而 `html_rules.md` 给正文的软上限是 10000 字（含标签）。按现存知识的字符构成
# （约 37% 中日韩 + 61% ASCII）推算，10000 字正文约 5600~8300 token，加上其余字段
# 大致 6000~9000，所以取 20000 留 2~3 倍余量。
# 关键是这个数**只是上限不是预算**：计费按实际输出 token，调大不多花钱；调小则把
# 「模型写超一点」从内容略长变成 JSON 中途截断、整次分析报错（见 `_parse_error_message`）。
# 反向的代价：部分 OpenAI 兼容模型的输出上限本身就是 16384 或 8192，超过会直接 400
# ——遇到就用环境变量下调。放模块级 + 环境变量是因为不同 base_url 后面挂的模型上限
# 差别很大，页面上不该为此多一个输入框，但运维必须能改。
# `llm.py` 的默认值不动——那是传输层，不该有业务判断。
DEFAULT_MAX_TOKENS = int(os.environ.get("PR_LEARNER_ANALYZE_MAX_TOKENS", "20000"))


def build_system_prompt() -> str:
    """组装 system 提示词：填入输出模板与 HTML 写法约定两块内容。

    白名单单独成 `analyze/html_rules.md`：它是**最常被调**的一块（要和前端消毒白名单
    对齐），塞进 system.md 会把那 9 行的可读性毁掉。
    """
    return prompts.render(
        "analyze/system.md",
        output_template=prompts.read("analyze/output.json"),
        html_rules=prompts.read("analyze/html_rules.md"),
    )


def build_user_prompt(pr_markdown: str) -> str:
    """组装 user 提示词：把 PR Markdown 填入 analyze/user.md 的 {pr_markdown} 占位。"""
    return prompts.render("analyze/user.md", pr_markdown=pr_markdown)


def _call_llm(
    base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS
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
    base_url: str, api_key: str, model: str, prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS
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


def _parse_error_message(raw: str, exc: Exception) -> str:
    """把「JSON 解析失败」翻成可操作的提示。

    HTML 正文动辄几千字，一旦顶到 max_tokens，模型的 JSON 会在半路断掉，而报错只说
    「未找到 JSON 对象」非常费解。开了 `{` 却没以 `}` 收尾几乎必然是被截断，直接点名
    可调的旋钮。**要求出现过 `{`**：否则模型拒答那种纯散文也会被误报成截断。
    """
    text = raw.strip()
    if "{" in text and not text.endswith("}"):
        return (
            f"模型输出疑似被 max_tokens 截断（已产出 {len(text)} 字，JSON 未闭合）。"
            f"可调大环境变量 PR_LEARNER_ANALYZE_MAX_TOKENS（当前 {DEFAULT_MAX_TOKENS}）"
            f"或让提示词产出更短的正文。原始错误: {exc}"
        )
    return str(exc)


def _fields_from_obj(obj: dict, pr, repo: str, number: int) -> dict:
    """把模型输出的 JSON 对象规整成草稿字段。

    返回键：title / category / content / content_format / tags / source_pr。
    `content_format` 的兜底：模型给了能认出来的值就听它的，没给或给了垃圾值才嗅探正文
    （`htmltext.detect_content_format` 宁可判成 markdown 也不误判成 html）。
    """
    tags = obj.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    content = str(obj.get("content", "")).strip()
    raw_format = obj.get("content_format")
    content_format = (
        store.normalize_content_format(raw_format)
        if store.is_known_content_format(raw_format)
        else htmltext.detect_content_format(content)
    )
    return {
        "title": str(obj.get("title", "")).strip() or pr.title,
        "category": str(obj.get("category", "")).strip() or "未分类",
        "content": content,
        "content_format": content_format,
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
    """拉取并分析一个 PR，返回草稿字段 dict（含 content_format）。"""
    pr = fetch_pr(repo, number)
    pr_md = to_markdown(pr)
    prompt = build_user_prompt(pr_md)

    raw = _call_llm(base_url, api_key, model, prompt)
    try:
        obj = _parse_json_object(raw)
    except AnalyzeError as exc:
        raise AnalyzeError(_parse_error_message(raw, exc)) from exc
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
    raw = "".join(chunks)
    try:
        obj = _parse_json_object(raw)
    except AnalyzeError as exc:
        yield {"type": "error", "message": _parse_error_message(raw, exc)}
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
