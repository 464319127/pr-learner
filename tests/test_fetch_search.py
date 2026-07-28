"""fetch.search_prs 的命令拼装与结果映射测试。

不打真实网络：monkeypatch _run_gh 捕获传给 gh 的 argv。这里断言的每一条
都对应一个实测过的 gh 行为陷阱，改动前先读 fetch.py 里的注释。
"""

import json

import pytest

from pr_learner import fetch


@pytest.fixture
def captured(monkeypatch):
    """替身 _run_gh：记录 argv，返回可配置的 stdout。"""
    box = {"args": None, "stdout": "[]"}

    def fake_run_gh(args, *, timeout=60.0):
        box["args"] = args
        box["timeout"] = timeout
        return box["stdout"]

    monkeypatch.setattr(fetch, "_run_gh", fake_run_gh)
    return box


# 一段真实 gh search prs --json 输出（结构照抄，内容裁剪）。
# 用真实嵌套结构而非手写扁平 dict，才能测出字段映射写错。
REAL_ITEM = {
    "author": {"id": "MDQ6VXNlcg==", "is_bot": False, "login": "hzh0425", "type": "User"},
    "body": "<!-- 模板注释 -->\r\n## Motivation\r\n修了 hicache 的内存问题。\r\n![img](https://x/y.png)\r\n<img src='z'>",
    "createdAt": "2026-07-27T06:49:50Z",
    "isDraft": False,
    "labels": [
        {"color": "0075ca", "description": "docs", "id": "LA_1", "name": "documentation"},
        {"color": "c5def5", "description": "", "id": "LA_2", "name": "hicache"},
    ],
    "number": 32484,
    "repository": {"name": "sglang", "nameWithOwner": "sgl-project/sglang"},
    "state": "open",
    "title": "[UnifiedTree]: move dir",
    "updatedAt": "2026-07-28T05:29:53Z",
    "url": "https://github.com/sgl-project/sglang/pull/32484",
}


def _q(args: list[str]) -> list[str]:
    """取出 `--` 之后的 query token。"""
    return args[args.index("--") + 1 :] if "--" in args else []


def test_query_split_into_tokens(captured) -> None:
    """整条查询作单个参数时 gh 会错误加引号并静默返空，必须逐 token 传。"""
    fetch.search_prs("is:merged fix cache", repo="cli/cli")
    assert _q(captured["args"]) == ["is:merged", "fix", "cache"]


def test_quoted_phrase_stays_one_token(captured) -> None:
    fetch.search_prs('"radix cache" eviction', repo="o/r")
    assert _q(captured["args"]) == ["radix cache", "eviction"]


def test_unbalanced_quote_falls_back(captured) -> None:
    """模型常吐半个引号，shlex 会抛 ValueError，此时退回粗暴切分而不是报错。"""
    fetch.search_prs('"radix cache eviction', repo="o/r")
    assert _q(captured["args"]) == ["radix", "cache", "eviction"]


def test_flags_precede_double_dash(captured) -> None:
    """argv 顺序必须是 flags → `--` → token：反过来 gh 会把 --repo 当查询词。"""
    fetch.search_prs("fix", repo="o/r", state="closed", sort="updated", limit=5)
    argv = captured["args"]
    dd = argv.index("--")
    for flag in ("--repo", "--state", "--merged=false", "--sort", "--limit", "--json"):
        assert argv.index(flag) < dd, f"{flag} 必须在 -- 之前"


def test_negative_qualifier_preserved(captured) -> None:
    """`-label:bug` 是合法排除语法；有 `--` 兜着就不会被当成命令行选项。"""
    fetch.search_prs("-label:bug fix", repo="o/r")
    assert _q(captured["args"]) == ["-label:bug", "fix"]


def test_scope_qualifiers_stripped(captured) -> None:
    """repo:/is:issue 与 --repo、gh 自动追加的 type:pr 冲突，会让结果恒空。"""
    fetch.search_prs("repo:other/x org:acme is:issue is:pr type:pr fix", repo="o/r")
    assert _q(captured["args"]) == ["fix"]


def test_state_open(captured) -> None:
    fetch.search_prs("fix", repo="o/r", state="open")
    assert "--state" in captured["args"]
    argv = captured["args"]
    assert argv[argv.index("--state") + 1] == "open"
    assert not any(a.startswith("--merged") for a in argv)


def test_state_merged_uses_boolean_flag(captured) -> None:
    """`--state merged` 会被 gh 拒绝退出；已合并只能用布尔 flag `--merged=true`。

    注意必须是 `=` 形式：写成 `--merged true` 时 true 会被当查询词，静默返回错结果。
    """
    fetch.search_prs("fix", repo="o/r", state="merged")
    argv = captured["args"]
    assert "--merged=true" in argv
    assert "--state" not in argv


def test_state_closed_excludes_merged(captured) -> None:
    """`--state closed` 实测**包含**已合并的 PR，所以「已关闭未合并」要叠 --merged=false。"""
    fetch.search_prs("fix", repo="o/r", state="closed")
    argv = captured["args"]
    assert argv[argv.index("--state") + 1] == "closed"
    assert "--merged=false" in argv


def test_unknown_state_ignored(captured) -> None:
    fetch.search_prs("fix", repo="o/r", state="draft")
    argv = captured["args"]
    assert "--state" not in argv
    assert not any(a.startswith("--merged") for a in argv)


@pytest.mark.parametrize("state", ["open", "merged", "closed"])
def test_state_qualifiers_stripped_when_state_given(captured, state) -> None:
    """下拉框选了状态就以它为准：query 里矛盾的状态限定符会让 gh 静默返空。"""
    fetch.search_prs("is:merged is:open state:closed fix", repo="o/r", state=state)
    assert _q(captured["args"]) == ["fix"]


def test_state_qualifiers_kept_when_no_state(captured) -> None:
    """没选状态时保留 query 里的 is:merged——那是用户/模型的显式意图。"""
    fetch.search_prs("is:merged fix", repo="o/r")
    assert _q(captured["args"]) == ["is:merged", "fix"]


def test_sort_none_omits_flag(captured) -> None:
    """不传 sort 时不能加 --sort：gh 默认就是 best-match，显式传它反而报错。"""
    fetch.search_prs("fix", repo="o/r")
    assert "--sort" not in captured["args"]

    fetch.search_prs("fix", repo="o/r", sort="best-match")
    assert "--sort" not in captured["args"]

    fetch.search_prs("fix", repo="o/r", sort="updated")
    assert "--sort" in captured["args"]


@pytest.mark.parametrize(("given", "expected"), [(0, "1"), (-5, "1"), (20, "20"), (999, "100")])
def test_limit_clamped(captured, given, expected) -> None:
    fetch.search_prs("fix", repo="o/r", limit=given)
    argv = captured["args"]
    assert argv[argv.index("--limit") + 1] == expected


def test_repo_only_search_allowed(captured) -> None:
    """只给仓库不给关键词是合法的（列该仓库的 PR），此时没有 `--`。"""
    fetch.search_prs("", repo="o/r", sort="updated")
    assert "--" not in captured["args"]


def test_empty_query_and_repo_rejected(captured) -> None:
    with pytest.raises(fetch.GhError):
        fetch.search_prs("")


def test_empty_stdout_returns_empty_list(captured) -> None:
    """空结果是 exit 0 + 空输出，属正常态，不能抛 JSONDecodeError。"""
    captured["stdout"] = ""
    assert fetch.search_prs("fix", repo="o/r") == []
    captured["stdout"] = "[]"
    assert fetch.search_prs("fix", repo="o/r") == []


def test_gh_error_propagates(monkeypatch) -> None:
    def boom(args, *, timeout=60.0):
        raise fetch.GhError("仓库不存在或无权访问，请检查仓库名")

    monkeypatch.setattr(fetch, "_run_gh", boom)
    with pytest.raises(fetch.GhError):
        fetch.search_prs("fix", repo="nope/nope")


def test_pr_summary_mapping_from_real_payload(captured) -> None:
    captured["stdout"] = json.dumps([REAL_ITEM])
    (pr,) = fetch.search_prs("fix", repo="sgl-project/sglang")

    assert pr.number == 32484
    assert pr.repo == "sgl-project/sglang"  # nameWithOwner，不是 name
    assert pr.author == "hzh0425"  # author.login
    assert pr.labels == ["documentation", "hicache"]  # labels[].name
    assert pr.state == "open"
    assert pr.created_at == "2026-07-27"  # 只留日期
    assert pr.updated_at == "2026-07-28"
    assert pr.is_draft is False
    assert pr.url.endswith("/pull/32484")


def test_excerpt_cleans_noise() -> None:
    """PR 描述常塞满模板注释和 <img>，不清掉的话提示词一半是图片链接。"""
    pr = fetch._to_pr_summary(REAL_ITEM)
    ex = pr.excerpt()
    assert "Motivation" in ex
    assert "<!--" not in ex and "模板注释" not in ex
    assert "<img" not in ex and ".png" not in ex
    assert "\r" not in ex and "\n" not in ex


def test_excerpt_truncates() -> None:
    pr = fetch.PrSummary(repo="o/r", number=1, title="t", url="u", body="x" * 500)
    ex = pr.excerpt(100)
    assert len(ex) == 101 and ex.endswith("…")


def test_as_dict_shape() -> None:
    d = fetch._to_pr_summary(REAL_ITEM).as_dict()
    assert set(d) == {
        "repo",
        "number",
        "title",
        "url",
        "author",
        "state",
        "labels",
        "created_at",
        "updated_at",
        "is_draft",
        "excerpt",
    }
    assert "body" not in d  # 原始正文不外泄，只给摘录
