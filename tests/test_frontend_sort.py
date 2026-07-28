"""前端结果排序逻辑的回归测试。

页面的 `sortedResults` 是纯 JS，Python 测不到，但它有两个容易写错且后端完全看不见的点：
排序方向的正负号（写反了「由新到旧」会变成「由旧到新」，而页面上按钮文案照样显示对的），
以及 `relevance` 档必须原样返回、不能就地 sort（`Array.prototype.sort` 会改原数组，
把后端排好的价值顺序永久打乱）。

做法：从 index.html 里抽出真实函数体交给 node 执行，而不是在测试里手抄一份逻辑——
手抄版只能证明「我抄的那份是对的」。没装 node 时跳过。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行前端 JS")

INDEX_HTML = Path(__file__).parent.parent / "src" / "pr_learner" / "static" / "index.html"

# 覆盖三种情况：日期正常、跨年（字符串比较必须仍等于时间序）、日期缺失
FIXTURE = [
    {"n": 1, "created_at": "2026-01-05", "updated_at": "2026-07-01"},
    {"n": 2, "created_at": "2025-12-31", "updated_at": "2026-07-28"},
    {"n": 3, "created_at": "", "updated_at": ""},
    {"n": 4, "created_at": "2026-07-20", "updated_at": "2026-07-20"},
]


def _sorted(sort_by: str, sort_desc: bool) -> list[int]:
    """在 node 里跑页面真实的 sortedResults，返回排序后的 n 序列。"""
    html = INDEX_HTML.read_text("utf-8")
    m = re.search(r"sortedResults\(\) \{([\s\S]*?)\n        \},", html)
    assert m, "index.html 里找不到 sortedResults（改名了？同步更新本测试）"

    script = f"""
    const body = {json.dumps(m.group(1))};
    const fn = new Function('return function(){{' + body + '}}')();
    const prResults = {json.dumps(FIXTURE)};
    const out = fn.call({{ prResults, sortBy: {json.dumps(sort_by)}, sortDesc: {json.dumps(sort_desc)} }});
    // 一并回传原数组，用于检查有没有被就地修改
    console.log(JSON.stringify({{
      sorted: out.map(x => x.n),
      original: prResults.map(x => x.n),
    }}));
    """
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    result = json.loads(proc.stdout)
    assert result["original"] == [1, 2, 3, 4], (
        "原数组被就地 sort 了：这会永久打乱后端排好的价值顺序，切回「价值」档也回不去"
    )
    return result["sorted"]


def test_relevance_keeps_backend_order() -> None:
    """价值档必须原样返回后端顺序——那是模型排的，前端不该动。"""
    assert _sorted("relevance", True) == [1, 2, 3, 4]
    assert _sorted("relevance", False) == [1, 2, 3, 4]


def test_created_desc_is_newest_first() -> None:
    """方向的正负号写反时，按钮文案仍显示「由新到旧」但结果是反的，只有断言能抓到。"""
    assert _sorted("created", True) == [4, 1, 2, 3]


def test_created_asc_is_oldest_first() -> None:
    assert _sorted("created", False) == [2, 1, 4, 3]


def test_updated_desc_and_asc() -> None:
    assert _sorted("updated", True) == [2, 4, 1, 3]
    assert _sorted("updated", False) == [1, 4, 2, 3]


@pytest.mark.parametrize(
    ("sort_by", "desc"),
    [("created", True), ("created", False), ("updated", True), ("updated", False)],
)
def test_missing_dates_sink_in_both_directions(sort_by, desc) -> None:
    """日期缺失的条目两个方向都沉底，不能因为空串字典序最小就浮到顶上。"""
    assert _sorted(sort_by, desc)[-1] == 3


def test_sort_options_match_frontend_keys() -> None:
    """下拉框的 value 必须和 sortedResults 认识的键一致，否则选了没反应。"""
    html = INDEX_HTML.read_text("utf-8")
    bar = re.search(r'<select v-model="sortBy">([\s\S]*?)</select>', html)
    assert bar, "找不到排序下拉框"
    values = set(re.findall(r'<option value="(\w+)"', bar.group(1)))
    assert values == {"relevance", "created", "updated"}, f"下拉框选项变了: {values}"
