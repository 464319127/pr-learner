# 交互记录 (prompt.md)

本文件记录用户与 agent 的每次交互内容。

---

## 约定
- 用户要求：把每次交互内容往 `prompt.md` 文件里记录。

---

## 交互历史

### 2026-07-24

1. 用中文回复和写文档。
2. 项目目标：用 agent 阅读 PR、分类记录知识，可能用 Python 脚本 + uv + docker compose。
3. 确认：PR 来自 GitHub（用 gh）、知识存为分类 Markdown、Ducc/Claude 直接读、需要知识查询 Web/API 服务。
4. 搭建项目骨架（fetch/store/query/api/cli + pyproject + Dockerfile + docker-compose + CLAUDE.md）。
5. 用 sglang PR #32188 试跑完整流程。
6. 修复 Docker 构建报错（README.md 未 COPY）。
7. 页面用 Vue 风格重写，并验证提交和查询功能。
8. 明确：记录知识是 agent 的工作，用户只 review；改为草稿制（agent 生成草稿 → 用户在页面 review 修改后保存）。
9. 页面直接提交 PR 地址：服务端调 LLM API（用户填 base_url/api_key/model）分析生成草稿。接口为 baidu-int oneapi 的 /v1/messages。
10. 查询页正文按 Markdown 渲染（marked + DOMPurify）。
11. 待审草稿页正文做成左右分屏：左编辑、右实时预览、滚动联动。
12. 要求："把我的每次交互内容往 prompt.md 文件里记录"，并把这句话记入项目记录文件。
