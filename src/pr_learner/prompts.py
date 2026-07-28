"""提示词模板读取：提示词维护在 prompt_templates/ 下的纯文本文件里。

用户和 agent 直接编辑模板文件即可调整提示词，无需改代码。目录按用途分子目录：

    prompt_templates/
        analyze/    system.md  user.md  output.json
        discover/   query_system.md  query_user.md
                    rank_system.md   rank_user.md  rank_output.json

占位符用 `{名字}` 书写，由 `render` 做**字面替换**。注意不能改用 str.format：
模板里会填入 JSON（满是 `{` `}`），format 会直接抛异常。
"""

from __future__ import annotations

from pathlib import Path

PROMPT_TEMPLATES_DIR = Path(__file__).parent / "prompt_templates"


def read(name: str) -> str:
    """读取模板文件内容（去掉首尾空白）。name 是相对 prompt_templates/ 的路径。"""
    return (PROMPT_TEMPLATES_DIR / name).read_text(encoding="utf-8").strip()


def render(name: str, **values: str) -> str:
    """读取模板并把 `{key}` 占位替换成 values 里的值。"""
    text = read(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
