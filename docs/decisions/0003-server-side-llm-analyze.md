# 0003 - 页面提交 PR 由服务端调用 LLM API 分析

- 日期：2026-07-24
- 状态：已采纳

## 背景

用户希望在页面直接提交 PR 地址，由系统分析生成草稿，而不是让 agent 在 CLI 会话里手动处理。

## 决定

新增 `POST /api/analyze`：服务端用 `gh` 拉取 PR，再调用用户在页面填写的 LLM 接口分析生成草稿。接口为 Anthropic messages 兼容格式：`POST {base_url}/v1/messages`，`Authorization: Bearer <key>`，默认 base_url 为 baidu-int oneapi（`https://oneapi-comate.baidu-int.com`）。响应解析同时兼容 Anthropic（`content[].text`）与 OpenAI（`choices[].message.content`）。

## 理由

- 用户体验：页面自助提交，无需 CLI 介入。
- 凭证由用户在页面填写（base_url/api_key/model），**仅本次请求转发给 LLM，不落盘、不记录**。

## 影响

- 新增 `analyze.py`，引入 `httpx` 依赖。
- 引入调用成本与外部依赖；与「不单独起 LLM 服务」的早期设想有偏离，经用户确认后采纳。
