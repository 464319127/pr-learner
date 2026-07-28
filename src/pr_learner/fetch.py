"""用 gh CLI 拉取 GitHub PR 的元信息、diff 和评论，以及搜索相关 PR。

约定：本地已经执行过 `gh auth login`。所有对 gh 的调用都通过 subprocess，
以列表形式传参避免命令注入。
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field


class GhError(RuntimeError):
    """gh CLI 调用失败时抛出。"""


# gh 的退出码 4 表示认证问题；容器里最常见的是 GH_TOKEN 没注入或已过期。
_GH_EXIT_AUTH = 4


def _gh_error_message(returncode: int, stderr: str) -> str:
    """把 gh 的英文报错翻成可操作的中文提示。"""
    if returncode == _GH_EXIT_AUTH:
        return "gh 未登录或凭证失效，请先执行 `gh auth login`；容器内需注入 GH_TOKEN"
    if "cannot be searched" in stderr:
        return "仓库不存在或无权访问，请检查仓库名"
    if "API rate limit" in stderr or "rate limit" in stderr.lower():
        return "GitHub 搜索接口限流（30 次/分钟），请稍后再试"
    return f"gh 调用失败: {stderr.strip()}"


def _run_gh(args: list[str], *, timeout: float = 60.0) -> str:
    """执行 gh 命令并返回 stdout。以列表传参，绝不拼接字符串。

    带 timeout：调用方多为同步生成器跑在 FastAPI threadpool 里，
    gh 卡在网络上会永久占住线程。
    """
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GhError("未找到 gh 命令，请先安装 GitHub CLI") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh 调用超时（{timeout:.0f}s），请检查网络") from exc
    except subprocess.CalledProcessError as exc:
        raise GhError(_gh_error_message(exc.returncode, exc.stderr or "")) from exc
    return proc.stdout


@dataclass
class PullRequest:
    """一个 PR 的完整内容，供 agent 阅读。"""

    repo: str
    number: int
    title: str
    author: str
    state: str
    url: str
    body: str
    diff: str
    comments: list[dict] = field(default_factory=list)
    review_comments: list[dict] = field(default_factory=list)


# gh pr view 需要的字段
_PR_FIELDS = "title,author,state,url,body,comments"


def fetch_pr(repo: str, number: int) -> PullRequest:
    """拉取单个 PR 的元信息、diff 和评论。

    Args:
        repo: "owner/name" 格式的仓库标识。
        number: PR 编号。
    """
    meta_raw = _run_gh(
        ["pr", "view", str(number), "--repo", repo, "--json", _PR_FIELDS]
    )
    meta = json.loads(meta_raw)

    diff = _run_gh(["pr", "diff", str(number), "--repo", repo])

    # inline review comments 走 API，pr view 拿不全
    review_comments_raw = _run_gh(
        [
            "api",
            f"repos/{repo}/pulls/{number}/comments",
            "--paginate",
        ]
    )
    review_comments = json.loads(review_comments_raw) if review_comments_raw.strip() else []

    return PullRequest(
        repo=repo,
        number=number,
        title=meta.get("title", ""),
        author=(meta.get("author") or {}).get("login", ""),
        state=meta.get("state", ""),
        url=meta.get("url", ""),
        body=meta.get("body", ""),
        diff=diff,
        comments=meta.get("comments", []),
        review_comments=review_comments,
    )


def to_markdown(pr: PullRequest, max_diff_lines: int = 2000) -> str:
    """把 PR 渲染成 agent 友好的 Markdown，方便直接阅读。"""
    lines = [
        f"# PR #{pr.number}: {pr.title}",
        "",
        f"- 仓库: {pr.repo}",
        f"- 作者: {pr.author}",
        f"- 状态: {pr.state}",
        f"- 链接: {pr.url}",
        "",
        "## 描述",
        "",
        pr.body or "（无描述）",
        "",
    ]

    if pr.comments:
        lines += ["## 讨论评论", ""]
        for c in pr.comments:
            author = (c.get("author") or {}).get("login", "?")
            lines.append(f"**{author}**: {c.get('body', '').strip()}")
            lines.append("")

    if pr.review_comments:
        lines += ["## 代码评审评论", ""]
        for c in pr.review_comments:
            path = c.get("path", "?")
            author = (c.get("user") or {}).get("login", "?")
            lines.append(f"- `{path}` — **{author}**: {c.get('body', '').strip()}")
        lines.append("")

    diff_lines = pr.diff.splitlines()
    truncated = len(diff_lines) > max_diff_lines
    shown = "\n".join(diff_lines[:max_diff_lines])
    lines += ["## Diff", "", "```diff", shown, "```"]
    if truncated:
        lines.append(f"\n> diff 过长，已截断（共 {len(diff_lines)} 行，展示前 {max_diff_lines} 行）")

    return "\n".join(lines)


# --- PR 搜索：按关键词在仓库里找相关 PR ---

# gh search prs 需要的字段
_SEARCH_FIELDS = "number,title,url,author,state,createdAt,updatedAt,labels,isDraft,repository,body"

# 状态筛选 → gh flag 的映射。实测要点：
# - `--state` 只接受 open|closed，传 merged 会被 gh 拒绝并退出；
# - `--state closed` **包含已合并的 PR**（实测 10 条里 5 条 merged），所以「已关闭未合并」
#   必须叠一个 `--merged=false`；
# - `--merged` 是布尔 flag，只能写 `--merged=true/false`。写成 `--merged false` 时
#   false 会被当成查询词，静默返回错的结果。
_STATE_FLAGS = {
    "open": ["--state", "open"],
    "merged": ["--merged=true"],
    "closed": ["--state", "closed", "--merged=false"],
}

# --sort 的合法值不含 best-match：不传 --sort 时 gh 默认就是 best-match，
# 显式传 best-match 反而报错退出。
_ALLOWED_SORT = {"created", "updated", "comments", "reactions", "interactions"}

# 必须从查询里剥掉的限定符前缀：
# - repo:/org:/user: 与 --repo 并存会让两个范围 AND 起来，gh 静默返回空列表；
# - is:issue/type:issue 与 gh 自动追加的 type:pr 冲突，结果恒空；
# - is:pr/type:pr 是冗余噪声。
_STRIP_PREFIXES = ("repo:", "org:", "user:", "is:issue", "type:issue", "is:pr", "type:pr")

# 显式指定状态筛选时要从查询里剥掉的状态限定符：两者矛盾时 gh 静默返回空列表
# （实测 `--state open` 配 `is:merged` → `[]`），而用户在下拉框里的选择是更明确的意图。
_STATE_PREFIXES = ("is:merged", "is:unmerged", "is:open", "is:closed", "state:", "merged:")

# 摘录清洗：HTML 标签、图片 Markdown、HTML 注释。PR 描述常塞满 <img> 和模板注释，
# 不清掉的话 30 条候选的提示词一半是图片链接。
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")


@dataclass
class PrSummary:
    """搜索结果里的一个 PR，只保留列表展示需要的字段。"""

    repo: str
    number: int
    title: str
    url: str
    author: str = ""
    state: str = ""
    labels: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    is_draft: bool = False
    body: str = ""

    def excerpt(self, chars: int = 400) -> str:
        """PR 描述的可读摘录：去掉注释/图片/标签，压平空白后截断。"""
        text = _RE_HTML_COMMENT.sub(" ", self.body or "")
        text = _RE_IMG_MD.sub(" ", text)
        text = _RE_HTML_TAG.sub(" ", text)
        text = " ".join(text.split())
        return text[:chars] + "…" if len(text) > chars else text

    def as_dict(self, *, excerpt_chars: int = 400) -> dict:
        """转成 API/CLI 友好的 dict，正文压成摘录避免响应与提示词过大。"""
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "state": self.state,
            "labels": self.labels,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_draft": self.is_draft,
            "excerpt": self.excerpt(excerpt_chars),
        }


def split_query(query: str) -> list[str]:
    """把查询串切成 token 列表。

    必须逐个 token 传给 gh：把整条查询作为单个参数时，gh 会错误地整体加引号
    （`"is:merged fix"` → `is:"merged fix"`），退出码 0 但返回空列表。
    shlex 能正确保留 `"radix cache"` 这类带引号短语为单 token；模型常吐出
    不闭合的引号，此时退回粗暴切分。
    """
    try:
        tokens = shlex.split(query or "", posix=True)
    except ValueError:
        tokens = (query or "").replace('"', " ").split()
    return [t for t in tokens if t]


def strip_scope_qualifiers(tokens: list[str]) -> list[str]:
    """剔除会与 --repo / type:pr 冲突的限定符，见 _STRIP_PREFIXES。"""
    return [t for t in tokens if not t.lower().startswith(_STRIP_PREFIXES)]


def strip_state_qualifiers(tokens: list[str]) -> list[str]:
    """剔除查询里的状态限定符，见 _STATE_PREFIXES。仅在显式指定 state 时调用。"""
    return [t for t in tokens if not t.lower().startswith(_STATE_PREFIXES)]


def search_prs(
    query: str = "",
    *,
    repo: str | None = None,
    limit: int = 20,
    state: str | None = None,
    sort: str | None = None,
    timeout: float = 30.0,
) -> list[PrSummary]:
    """用 `gh search prs` 搜索 PR，返回结构化列表。空结果是正常态，返回空列表。

    Args:
        query: GitHub 搜索语法，可含 `is:merged`、`label:bug`、`-label:x` 等限定符。
        repo: 限定 "owner/name" 仓库；query 为空时必须给 repo。
        limit: 最多返回条数，收敛到 1..100（gh 上限是 1000，但 >100 会翻多页多耗限额）。
        state: "open" / "merged" / "closed"（closed 指已关闭未合并）；其它值忽略。
            指定时会剥掉 query 里矛盾的状态限定符。
        sort: 见 _ALLOWED_SORT；不传则用 GitHub 的相关度排序。
    """
    tokens = strip_scope_qualifiers(split_query(query))
    state_flags = _STATE_FLAGS.get(state or "")
    if state_flags:
        tokens = strip_state_qualifiers(tokens)
    if not tokens and not repo:
        raise GhError("搜索至少需要关键词或仓库名")

    # argv 顺序必须是「flags 在前、`--`、再 query token」：
    # `--` 之后 gh 不再把 `-label:bug` 当命令行选项，也不会把 --repo 当查询词。
    args = ["search", "prs"]
    if repo:
        args += ["--repo", repo]
    if state_flags:
        args += state_flags
    if sort in _ALLOWED_SORT:
        args += ["--sort", sort]
    args += ["--limit", str(max(1, min(int(limit), 100))), "--json", _SEARCH_FIELDS]
    if tokens:
        args += ["--", *tokens]

    raw = _run_gh(args, timeout=timeout)
    items = json.loads(raw) if raw.strip() else []
    return [_to_pr_summary(it) for it in items]


def _to_pr_summary(item: dict) -> PrSummary:
    """把 gh search prs 的一条 JSON 结果规整成 PrSummary。"""
    repository = item.get("repository") or {}
    return PrSummary(
        # 取 nameWithOwner 而非调用方传入的 repo：跨仓库搜索时结果是混合的。
        repo=repository.get("nameWithOwner", ""),
        number=int(item.get("number", 0)),
        title=item.get("title", ""),
        url=item.get("url", ""),
        author=(item.get("author") or {}).get("login", ""),
        # 取值域是 open/closed/merged 三值，展示层别按二值处理。
        state=(item.get("state") or "").lower(),
        labels=[lb.get("name", "") for lb in (item.get("labels") or []) if lb.get("name")],
        created_at=(item.get("createdAt") or "")[:10],
        updated_at=(item.get("updatedAt") or "")[:10],
        is_draft=bool(item.get("isDraft")),
        body=item.get("body") or "",
    )
