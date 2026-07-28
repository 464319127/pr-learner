"""LLM 辅助的 PR 检索：关键词 + 仓库名 → 排好序、带中文简介的相关 PR 列表。

LLM 在这里承担两段工作：

1. `build_query` —— 把自然语言（常是中文）翻译成 GitHub 搜索语法。这一步不可省：
   GitHub 不索引中文，中文词直接搜实测返回空列表。
2. `rank_prs` —— `gh search prs` 拿到的候选是关键词匹配，混入无关 PR 是常态；
   让模型筛选、排序，并为每条写一句中文简介。

两段之间夹着真实的 gh 调用，所以结果里的 number/url 全部来自 GitHub，
模型只能重排和描述，不能凭空造出 PR（见 `rank_prs` 的 join 逻辑）。

依赖 llm + prompts + fetch，不依赖 analyze：查找与分析是两条独立业务。
提示词模板在 prompt_templates/discover/ 下，可直接编辑。
"""

from __future__ import annotations

import json

from pr_learner import llm, prompts
from pr_learner.fetch import GhError, PrSummary, search_prs, strip_scope_qualifiers

DEFAULT_BASE_URL = llm.DEFAULT_BASE_URL

# 进 rank 提示词的候选上限：再多会挤爆上下文，且相关度靠后的本来也不值得读。
MAX_RANK_CANDIDATES = 30

# 生成搜索语法是几十 token 的小活，别用默认的两分钟超时拖住交互式页面。
QUERY_TIMEOUT = 30.0

_RELEVANCE_ALIASES = {
    "高": "高",
    "high": "高",
    "很高": "高",
    "强": "高",
    "中": "中",
    "medium": "中",
    "中等": "中",
    "一般": "中",
    "低": "低",
    "low": "低",
    "弱": "低",
}


def normalize_relevance(value: object) -> str:
    """把模型给的相关度归一成 高/中/低，认不出就当「中」。

    排序不依赖这个字段（以模型返回的数组顺序为准），它只是展示标签。
    """
    return _RELEVANCE_ALIASES.get(str(value or "").strip().lower(), "中")


def _clean_query(raw: str) -> str:
    """把模型输出清成一行可用的搜索语法。"""
    text = (raw or "").strip()
    # 去掉可能的 markdown 围栏
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```"))
    # 只取第一行非空内容
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    # 模型有时把整行用引号包起来，那会变成一个精确短语
    if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
        line = line[1:-1].strip()
    # repo:/is:issue 等限定符会让结果恒空，必须剥掉（提示词里也写了，这里是双保险）
    return " ".join(strip_scope_qualifiers(line.split()))


def build_query(
    keyword: str,
    repo: str,
    *,
    api_key: str,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[str, str | None]:
    """把自然语言关键词翻成 GitHub 搜索语法。

    Returns:
        (query, warning)。warning 非空表示走了降级路径（模型失败或输出为空），
        query 回退成原始关键词——中文关键词在这种情况下大概率搜不到东西，
        调用方须把 warning 透给用户，否则用户会误以为「没有相关 PR」。
    """
    if not api_key:
        return keyword.strip(), None

    prompt = prompts.render("discover/query_user.md", keyword=keyword, repo=repo)
    try:
        raw = llm.call(
            base_url,
            api_key,
            model,
            prompt,
            system=prompts.read("discover/query_system.md"),
            max_tokens=200,
            timeout=QUERY_TIMEOUT,
        )
    except llm.LlmError as exc:
        return keyword.strip(), f"生成搜索语法失败（{exc}），已按原文检索"

    query = _clean_query(raw)
    if not query:
        return keyword.strip(), "模型未给出搜索语法，已按原文检索"
    return query, None


def _candidates_json(candidates: list[PrSummary]) -> str:
    """把候选压成进提示词的 JSON：只留模型判断相关度需要的字段。"""
    return json.dumps(
        [
            {
                "number": c.number,
                "title": c.title,
                "state": c.state,
                "labels": c.labels,
                "excerpt": c.excerpt(300),
            }
            for c in candidates
        ],
        ensure_ascii=False,
        indent=1,
    )


def _fallback_results(candidates: list[PrSummary]) -> list[dict]:
    """降级结果：保持 gh 的原始顺序，简介退化成 PR 描述摘录。"""
    return [{**c.as_dict(), "summary": "", "relevance": "中"} for c in candidates]


def join_ranked(ranked: list[dict], candidates: list[PrSummary]) -> list[dict]:
    """按 number 把模型的排序/简介 join 回真实候选。

    模型只能重排和描述真实存在的 PR：
    - 候选里没有的 number 一律丢弃（防幻觉）；
    - number 可能是字符串 "123"，统一转 int 再比；
    - 重复 number 只保留第一条；
    - url 只取候选里 gh 返回的，绝不用模型输出里的。
    """
    by_number = {c.number: c for c in candidates}
    out: list[dict] = []
    seen: set[int] = set()
    for item in ranked:
        if not isinstance(item, dict):
            continue
        try:
            number = int(str(item.get("number", "")).strip())
        except (TypeError, ValueError):
            continue
        if number not in by_number or number in seen:
            continue
        seen.add(number)
        out.append(
            {
                **by_number[number].as_dict(),
                "summary": str(item.get("summary", "")).strip(),
                "relevance": normalize_relevance(item.get("relevance")),
            }
        )
    return out


def _rank_system_prompt() -> str:
    return prompts.render(
        "discover/rank_system.md",
        output_template=prompts.read("discover/rank_output.json"),
    )


def _rank_user_prompt(keyword: str, candidates: list[PrSummary]) -> str:
    return prompts.render(
        "discover/rank_user.md",
        keyword=keyword,
        candidates=_candidates_json(candidates),
    )


def rank_prs(
    keyword: str,
    candidates: list[PrSummary],
    *,
    api_key: str,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict]:
    """让 LLM 筛选、排序候选并写中文简介。失败时降级为原始顺序，不抛异常。"""
    if not candidates or not api_key:
        return _fallback_results(candidates)

    head = candidates[:MAX_RANK_CANDIDATES]
    try:
        raw = llm.call(
            base_url,
            api_key,
            model,
            _rank_user_prompt(keyword, head),
            system=_rank_system_prompt(),
        )
        obj = llm.parse_json_object(raw)
    except llm.LlmError:
        return _fallback_results(head)

    results = obj.get("results")
    if not isinstance(results, list):
        return _fallback_results(head)
    return join_ranked(results, head)


def discover_prs_stream(
    keyword: str,
    repo: str,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = DEFAULT_BASE_URL,
    limit: int = 20,
    state: str | None = None,
):
    """流式查找 PR，逐个 yield 事件 dict，供服务端转成 SSE 推给页面。

    事件类型：
    - {"type":"status","message":...}      阶段提示
    - {"type":"query","query":...}         最终使用的 GitHub 搜索语法
    - {"type":"candidates","count":N}      gh 搜到的候选条数
    - {"type":"thinking","text":...}       模型思考过程增量
    - {"type":"text","text":...}           模型输出增量
    - {"type":"result","results":[...]}    最终列表（可能为空，空不是错误）
    - {"type":"error","message":...}       出错
    """
    keyword = (keyword or "").strip()
    if not keyword and not repo:
        yield {"type": "error", "message": "请至少填写关键词或仓库名"}
        return

    if api_key:
        yield {"type": "status", "message": "正在把关键词翻译成 GitHub 搜索语法 …"}
    query, warning = build_query(keyword, repo, api_key=api_key, model=model, base_url=base_url)
    if warning:
        yield {"type": "status", "message": f"⚠ {warning}"}

    yield {"type": "query", "query": query}
    yield {"type": "status", "message": f"正在搜索 {repo or 'GitHub'} …"}

    try:
        candidates = search_prs(query, repo=repo or None, limit=limit, state=state)
    except GhError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    # 搜不到时兜底：按最近更新列出该仓库的 PR，好过让用户对着空列表发呆。
    # 中文关键词 + 翻译失败最容易走到这里。
    if not candidates and repo:
        yield {"type": "status", "message": "该查询没有命中，改为列出最近更新的 PR …"}
        try:
            candidates = search_prs("", repo=repo, limit=limit, state=state, sort="updated")
        except GhError as exc:
            yield {"type": "error", "message": str(exc)}
            return

    yield {"type": "candidates", "count": len(candidates)}
    if not candidates:
        yield {"type": "status", "message": "没有找到相关 PR，换个关键词试试"}
        yield {"type": "result", "results": []}
        return

    if not api_key:
        yield {"type": "status", "message": "未提供 API Key，跳过模型筛选，直接返回搜索结果"}
        yield {"type": "result", "results": _fallback_results(candidates)}
        return

    head = candidates[:MAX_RANK_CANDIDATES]
    yield {
        "type": "status",
        "message": f"搜到 {len(candidates)} 条，正在让模型筛选前 {len(head)} 条并写简介 …",
    }

    chunks: list[str] = []
    try:
        for kind, text in llm.iter_stream(
            base_url,
            api_key,
            model,
            _rank_user_prompt(keyword, head),
            system=_rank_system_prompt(),
        ):
            if kind == "text":
                chunks.append(text)
            yield {"type": kind, "text": text}
        obj = llm.parse_json_object("".join(chunks))
        results = obj.get("results")
        if not isinstance(results, list):
            raise llm.LlmError("模型输出缺少 results 数组")
        ranked = join_ranked(results, head)
    except llm.LlmError as exc:
        # 模型抽风时降级成未排序的搜索结果，而不是让用户什么都看不到。
        yield {"type": "status", "message": f"⚠ 模型筛选失败（{exc}），改为直接展示搜索结果"}
        ranked = _fallback_results(head)

    if not ranked:
        yield {"type": "status", "message": "模型认为候选都不相关，已展示原始搜索结果"}
        ranked = _fallback_results(head)

    yield {"type": "status", "message": f"✓ 完成，共 {len(ranked)} 条"}
    yield {"type": "result", "results": ranked}


def discover_prs(
    keyword: str,
    repo: str,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = DEFAULT_BASE_URL,
    limit: int = 20,
    state: str | None = None,
) -> dict:
    """非流式查找 PR，返回 {"query":..., "results":[...]}。GhError 原样冒泡。"""
    query, warning = build_query(keyword, repo, api_key=api_key, model=model, base_url=base_url)
    candidates = search_prs(query, repo=repo or None, limit=limit, state=state)
    results = rank_prs(keyword, candidates, api_key=api_key, model=model, base_url=base_url)
    return {"query": query, "warning": warning or "", "results": results}
