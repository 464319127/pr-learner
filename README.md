# pr-learner

用 agent 阅读 GitHub PR，并把读到的知识分类记录成 Markdown 知识库，提供 Web/API 检索。

## 工作方式

agent 是阅读和总结的主体，Python 只提供工具：

1. **拉取** — `gh` CLI 拉取 PR 的描述、diff、评论，渲染成 Markdown。
2. **阅读** — agent 阅读并总结出可复用的知识。
3. **记录** — 按分类写入 `knowledge/<分类>/<标题>.md`。
4. **检索** — 按关键词/分类/标签查询，或起 Web/API 服务查询。

## 快速开始

前提：本地已 `gh auth login`，已安装 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                                   # 安装依赖
uv run pr-learner fetch owner/repo 123    # 拉取 PR 供阅读
uv run pr-learner save "标题" 分类 内容.md  # 保存知识
uv run pr-learner search --keyword 重试    # 检索
uv run uvicorn pr_learner.api:app --port 8000   # 起查询服务
docker compose up --build                 # 容器化查询服务
```

更多命令与架构见 [CLAUDE.md](./CLAUDE.md)。
