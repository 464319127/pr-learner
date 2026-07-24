# docs/handoff.md

> 交接文档。新会话请先读 `AGENTS.md` 和本文件。最后更新：2026-07-24

## Goal

搭建 pr-learner：用 agent 阅读 GitHub PR，分类沉淀知识库，并提供 Web/API 供提交分析与检索。

## Completed

- 项目骨架：`fetch/store/query/api/cli/drafts/analyze` + `pyproject.toml`（uv）+ `Dockerfile` + `docker-compose.yml`。
- 拉取 PR（`gh`，subprocess 列表传参防注入）→ Markdown。
- 分类 Markdown 知识库 + 自动 `index.md`；草稿制（`knowledge/.drafts/`）。
- FastAPI 服务：查询、分类统计、草稿 CRUD、approve 转正、`/api/analyze`（服务端调 LLM 生成草稿）。
- Vue 单页前端：分析 PR / 查询 / 待审草稿三页；查询正文 Markdown 渲染（marked + DOMPurify）；草稿页左右分屏（编辑 + 实时预览 + 滚动联动）。
- 已用 sglang PR #32188 跑通完整链路，知识库现有 2 条真实知识。
- 项目知识文档：`AGENTS.md`、`docs/decisions/0001~0003`、`TODO.md`、`prompt.md`（带 `#log`/`#记录` 标记的交互 hook 自动记录）。

## Decisions

- 知识存分类 Markdown（可 git、可读、简单）——见 decisions/0001。
- 记录采用草稿制，agent 生成 / 用户 review——见 decisions/0002。
- 页面提交 PR 由服务端调 LLM API 分析（Anthropic messages 兼容，默认 baidu-int oneapi）——见 decisions/0003。

## Open Problems

- LLM 真实调用仅由用户用真实 key 跑通一次；`analyze` 的单测用 mock，未覆盖真实接口异常分支。
- 服务无鉴权，仅限本地/内网。
- `.claude/settings.local.json` 与 `prompt.md` 是否纳入/忽略 git 尚未定。
- hook 需 `/hooks` 或重启后才在本会话生效。

## Next Steps

1. 决定 `prompt.md`、`.claude/settings.local.json` 的 git 归属（提交 or gitignore）。
2. 首次提交项目知识文档与代码到 git。
3. 视需要为 `analyze` 增加真实接口的错误处理测试。

## Verification

- `uv run --group dev pytest -q` → 16 passed
- 手动起服务验证 `/api/analyze`、草稿 approve、查询 Markdown 渲染、分屏预览均正常
