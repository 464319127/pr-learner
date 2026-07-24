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
