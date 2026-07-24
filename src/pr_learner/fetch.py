"""用 gh CLI 拉取 GitHub PR 的元信息、diff 和评论。

约定：本地已经执行过 `gh auth login`。所有对 gh 的调用都通过 subprocess，
以列表形式传参避免命令注入。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


class GhError(RuntimeError):
    """gh CLI 调用失败时抛出。"""


def _run_gh(args: list[str]) -> str:
    """执行 gh 命令并返回 stdout。以列表传参，绝不拼接字符串。"""
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise GhError("未找到 gh 命令，请先安装 GitHub CLI") from exc
    except subprocess.CalledProcessError as exc:
        raise GhError(f"gh 调用失败: {exc.stderr.strip()}") from exc
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
