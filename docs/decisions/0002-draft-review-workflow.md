# 0002 - 记录采用草稿制（agent 生成，用户 review）

- 日期：2026-07-24
- 状态：已采纳

## 背景

最初页面让用户从零手填知识。用户明确：记录知识是 agent 的工作，用户只需 review。

## 决定

改为草稿制：agent（或服务端调 LLM）分析 PR → 生成草稿存 `knowledge/.drafts/*.json` → 用户在页面「待审草稿」编辑 → approve 转正入正式库并删除草稿。

## 理由

- 降低用户负担，用户只审校不从零写。
- 草稿与正式库隔离，未审内容不污染查询。
- approve 时以用户编辑后的最终内容为准。

## 影响

- 新增 `drafts.py` 与 `/api/drafts`、`/api/drafts/{id}/approve`、`DELETE /api/drafts/{id}`。
- 页面 Tab 为「分析 PR / 查询 / 待审草稿」，草稿页正文左右分屏（编辑 + 实时预览，滚动联动）。
