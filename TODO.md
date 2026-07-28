# TODO

## 待办

- [ ] 决定 `prompt.md`、`.claude/settings.local.json` 的 git 归属（提交或加入 `.gitignore`）。
- [ ] 用真实 key 跑几轮页面「查找 PR」，评估查询翻译与中文简介质量，按需调
      `prompt_templates/discover/*.md`（改模板不用改代码）。
- [x] 为 LLM 传输层增加接口错误处理测试（超时、非 200、格式异常、SSE 回退）——
      `tests/test_llm.py` 用 `httpx.MockTransport` 覆盖。

## 想法（非必须）

- [ ] 知识库规模变大后，评估 `query` 层换 SQLite/全文索引。
- [ ] 查询页支持按标签点选筛选、按日期排序。
- [ ] 给 GitHub search 加本地限流或结果缓存（接口限流 30 次/分钟）。
- [ ] 查找结果标注「已沉淀过」：比对知识库的 `source_pr` 字段，避免重复分析同一个 PR。
