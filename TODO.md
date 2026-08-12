# TODO

## 待办

- [ ] 决定 `prompt.md`、`.claude/settings.local.json` 的 git 归属（提交或加入 `.gitignore`）。
- [ ] 用真实 key 跑几轮页面「查找 PR」，评估查询翻译与中文简介质量，按需调
      `prompt_templates/discover/*.md`（改模板不用改代码）。
- [x] 现存 8 条知识已跑 `pr-learner convert --apply` 收敛成 `.html`（2026-08-12）。
      原 `.md` 的 blob 仍在 git（6 条在 index、2 条在 HEAD），`git checkout -- knowledge` 可整体回滚。
      副作用：源 markdown 里唯一的那个代码块 fence 没写语言，所以转出的 `<pre><code>` 不带
      `language-XXX`、**不着色**——这个信息源头就没有，只能靠重新分析补。
- [x] 真实模型的 HTML 正文质量已核验（2026-08-12，用户在页面上跑的那条「累积型误差…ReplaySSM」）：
      10321 字（软上限 10000 只超 3%）、**零白名单外标签与 class**、6 张表格全部包了 `table-wrap`、
      3 张卡片用了 tip/warn/note 三种语气、mermaid / 分栏 / 指标 / 结论各一处、
      `language-python` 与 `language-diff` 都在 hljs 包里、3 个代码块**无漏转义的裸标签**。
      未用到的只有 `<details>`（这条没有需要折叠的长内容）。提示词契约按预期生效，暂不需要调。
- [x] 为 LLM 传输层增加接口错误处理测试（超时、非 200、格式异常、SSE 回退）——
      `tests/test_llm.py` 用 `httpx.MockTransport` 覆盖。

## 想法（非必须）

- [ ] 知识库规模变大后，评估 `query` 层换 SQLite/全文索引。
- [ ] 查询页支持按标签点选筛选、按日期排序。
- [ ] 给 GitHub search 加本地限流或结果缓存（接口限流 30 次/分钟）。
- [ ] 查找结果标注「已沉淀过」：比对知识库的 `source_pr` 字段，避免重复分析同一个 PR。
- [ ] 深色模式。目前整站是浅色但代码块是 github-dark 的深底，混搭本来就不协调；
      要做就得连 `.rich-body` 的卡片五色一起重新定色板，不是加个 `prefers-color-scheme` 就完事。
- [ ] `syncScroll` 在 HTML 正文下更不准（一张 mermaid 图能占掉预览区一半高度）。
      它本来就是按比例的近似，没修是因为「按元素对齐」要给编辑器加行号映射，不值这个复杂度。
- [ ] `.md` 与 `.html` 长期并存的清理：两条渲染路径、两套测试都要维护。
      等历史知识全部 `convert` 完、且确认不再手写 Markdown 后，才能考虑收敛成单格式
      （`store.normalize_content_format` 与前端 markdown 分支是那时唯一要动的地方）。
