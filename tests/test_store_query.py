"""store / query 的基础测试，使用临时目录避免污染真实知识库。"""

from pathlib import Path

from pr_learner import query, store


def test_save_and_search(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    store.save_knowledge(
        title="重试与幂等",
        category="可靠性",
        content="PR 中通过幂等键避免重复扣款。",
        tags=["重试", "幂等"],
        source_pr="owner/repo#42",
        knowledge_dir=kdir,
    )

    # 关键词命中
    hits = query.search("幂等", knowledge_dir=kdir)
    assert len(hits) == 1
    assert hits[0]["title"] == "重试与幂等"
    assert hits[0]["source_pr"] == "owner/repo#42"

    # 分类过滤
    assert query.search(category="可靠性", knowledge_dir=kdir)
    assert not query.search(category="不存在", knowledge_dir=kdir)

    # 标签过滤
    assert query.search(tag="重试", knowledge_dir=kdir)

    # 分类统计
    assert query.list_categories(knowledge_dir=kdir) == {"可靠性": 1}


def test_index_generated(tmp_path: Path) -> None:
    kdir = tmp_path / "knowledge"
    store.save_knowledge("标题A", "分类X", "内容", knowledge_dir=kdir)
    index = (kdir / "index.md").read_text("utf-8")
    assert "标题A" in index
    assert "分类X" in index


# --- HTML 正文（docs/decisions/0005）---


def test_html_knowledge_lands_as_html_file(tmp_path: Path) -> None:
    """content_format=html 落 .html，frontmatter 也自解释地记一份。"""
    kdir = tmp_path / "knowledge"
    path = store.save_knowledge(
        "内核融合",
        "cuda",
        "<p>正文</p><p>第二段</p>",
        content_format="html",
        knowledge_dir=kdir,
    )
    assert path.suffix == ".html"
    assert "content_format: html" in path.read_text("utf-8")

    items = store.load_all(kdir)
    assert [it["content_format"] for it in items] == ["html"]


def test_load_all_sees_both_formats(tmp_path: Path) -> None:
    """两种扩展名同时可见，格式由扩展名判定。"""
    kdir = tmp_path / "knowledge"
    store.save_knowledge("老知识", "分类", "## md 正文", knowledge_dir=kdir)
    store.save_knowledge(
        "新知识", "分类", "<p>a</p><p>b</p>", content_format="html", knowledge_dir=kdir
    )

    got = {it["title"]: it["content_format"] for it in store.load_all(kdir)}
    assert got == {"老知识": "markdown", "新知识": "html"}


def test_search_strips_html_tags(tmp_path: Path) -> None:
    """HTML 正文按纯文本检索：能搜到被行内标签切开的词，搜不到标签名。"""
    kdir = tmp_path / "knowledge"
    store.save_knowledge(
        "幂等重试",
        "可靠性",
        "<div><p>通过<strong>幂等</strong>键避免重复扣款。</p></div>",
        content_format="html",
        knowledge_dir=kdir,
    )
    assert query.search("幂等键", knowledge_dir=kdir)
    assert not query.search("div", knowledge_dir=kdir)
    assert not query.search("strong", knowledge_dir=kdir)


def test_changing_format_replaces_old_file(tmp_path: Path) -> None:
    """同一条知识换格式后只剩一个文件，index.md 不出现重复项。"""
    kdir = tmp_path / "knowledge"
    md_path = store.save_knowledge("同一标题", "分类", "## 旧", knowledge_dir=kdir)
    html_path = store.save_knowledge(
        "同一标题", "分类", "<p>新</p><p>正文</p>", content_format="html", knowledge_dir=kdir
    )

    assert not md_path.exists()
    assert html_path.exists()
    assert len(store.load_all(kdir)) == 1
    index = (kdir / "index.md").read_text("utf-8")
    # 标题在同一行里既是链接文字又是文件名，所以数条目行而不是数标题出现次数
    assert len([ln for ln in index.splitlines() if ln.startswith("- [")]) == 1
    assert "同一标题.md" not in index


def test_load_all_skips_hidden_dirs(tmp_path: Path) -> None:
    """`.drafts/` 里放什么扩展名都不会串进正式知识库——这是显式不变量。"""
    kdir = tmp_path / "knowledge"
    store.save_knowledge("正式知识", "分类", "正文", knowledge_dir=kdir)
    drafts = kdir / ".drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "x.html").write_text("---\ntitle: 草稿\n---\n<p>a</p>", encoding="utf-8")
    (drafts / "y.md").write_text("---\ntitle: 草稿\n---\nmd", encoding="utf-8")

    assert [it["title"] for it in store.load_all(kdir)] == ["正式知识"]


def test_normalize_content_format_never_raises() -> None:
    """这个值可能来自模型输出或手写 JSON，未知一律回落 markdown。"""
    assert store.normalize_content_format("HTML") == "html"
    assert store.normalize_content_format(" Html ") == "html"
    assert store.normalize_content_format("md") == "markdown"
    for bad in [None, "", "xml", 42, [], "text/plain"]:
        assert store.normalize_content_format(bad) == "markdown"


def test_rewrite_as_keeps_original_date(tmp_path: Path) -> None:
    """convert 不能把历史知识的 date 改成今天。"""
    kdir = tmp_path / "knowledge"
    path = store.save_knowledge(
        "历史知识", "分类", "## 旧正文", tags=["a"], source_pr="o/r#1", knowledge_dir=kdir
    )
    import frontmatter

    post = frontmatter.load(path)
    post["date"] = "2020-01-02"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    new_path = store.rewrite_as(path, "<h2>旧正文</h2><p>x</p>", "html")
    assert new_path.suffix == ".html"
    assert not path.exists()

    after = frontmatter.load(new_path)
    assert after["date"] == "2020-01-02"
    assert after["tags"] == ["a"]
    assert after["source_pr"] == "o/r#1"
    assert after["content_format"] == "html"
