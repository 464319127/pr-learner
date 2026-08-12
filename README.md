# pr-learner

用 agent 阅读 GitHub PR，把读到的知识分类沉淀成文件知识库，并提供 Web/API 提交分析与检索。
正文默认是**受限 HTML 片段**：代码高亮、对比表格、mermaid 图、折叠区块与卡片排版都能用（历史 Markdown 继续可读）。

## 工作方式

记录知识是 agent 的工作，用户只做 review。数据流：`查找 PR → 拉取 PR → LLM/agent 分析 → 生成草稿 → 用户 review → 入库 → 检索`。

0. **查找** — 只有一个模糊问题时，在「查找 PR」页填关键词（可中文）+ 仓库名：
   LLM 把关键词翻成 GitHub 搜索语法，`gh` 搜出真实候选，LLM 再筛选排序并为每条写一句中文简介。
   状态可选「仅已合并 / 仅未关闭 / 已关闭未合并」，结果可按价值 / 创建时间 / 更新时间重排，
   每条都有跳转链接和「分析此 PR」按钮，一键接到下一步。
1. **提交** — 在页面填 PR 地址 + LLM 凭证（base_url/api_key/model，两个页面共用一份）。
2. **分析** — 服务端用 `gh` 拉取 PR，调 LLM 分析生成草稿。
3. **review** — 在「待审草稿」页编辑标题/分类/标签/正文（左右分屏，右侧实时预览，可切 HTML / Markdown），确认后入库。
   预览区若出现「⚠ 已移除 N 处不允许的标签或属性」，说明模型写了白名单外的东西（最常见是代码里的
   `vector<int>` 没转义成 `vector&lt;int&gt;`，不改的话入库后那半行代码会**看不见**）。
4. **检索** — 按关键词/分类/标签查询已沉淀的知识。折叠态给三行纯文本摘录，展开才做富渲染
   （代码高亮、表格横滚、mermaid 出图、`<details>` 可折叠）。

> 查找 PR 的能力边界：GitHub 搜索只索引 PR 的标题、描述和评论，**不索引 diff**，
> 所以「哪个 PR 改了某个函数」这类问题可能找不到。另外 GitHub 搜索接口限流 30 次/分钟。

## 快速开始

前提：本地已 `gh auth login`，已安装 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                                          # 安装依赖
uv run uvicorn pr_learner.api:app --port 8000    # 起服务，浏览器打开 http://localhost:8000
```

也可用 Docker：

```bash
cp .env.example .env          # 首次：填入 GitHub token
echo "GH_TOKEN=$(gh auth token)" > .env   # 或直接用本机 gh 登录态生成
docker compose up --build -d
```

> Docker 说明：容器内用 `gh` 拉取 PR，需要 GitHub 凭证。镜像已内置 `gh`，
> token 通过 `.env` 的 `GH_TOKEN` 注入（`.env` 已被 git 忽略，不会提交）。
> macOS 上 gh 的 token 存在系统钥匙串、不在 `~/.config/gh` 目录，所以必须走
> `GH_TOKEN`，仅挂载配置目录不够。

> 说明：服务无内置鉴权，仅面向本地/内网。公网暴露需前置网关加访问控制。

## 重启服务（项目更新后）

拉取新代码后按下面步骤重启。

### 本地（uvicorn）

```bash
git pull                                         # 拉取最新代码
uv sync                                          # 依赖若有变化则同步
# 停掉旧进程（Ctrl-C 前台运行的那个；或用下面命令按端口杀）
lsof -ti:8000 | xargs kill -9 2>/dev/null
uv run uvicorn pr_learner.api:app --port 8000    # 重新启动
```

开发时可加 `--reload`，改代码自动重启（无需手动重启）：

```bash
uv run uvicorn pr_learner.api:app --port 8000 --reload
```

### Docker

```bash
git pull
docker compose up --build -d     # 重新构建镜像并后台重启（自动读取 .env 里的 GH_TOKEN）
docker compose logs -f           # 查看日志
docker compose down              # 停止
```

> token 过期时重新生成 `.env`：`echo "GH_TOKEN=$(gh auth token)" > .env` 再 `docker compose up -d`。

## 命令行用法

```bash
uv run pr-learner discover "cache" --repo owner/repo --state merged   # 搜索相关 PR
uv run pr-learner fetch owner/repo 123                 # 拉取 PR，输出 Markdown 供阅读
uv run pr-learner draft "标题" 分类 内容.md            # 生成待审草稿（供页面 review）
uv run pr-learner save "标题" 分类 正文.html --content-format html   # 正文按 HTML 入库
uv run pr-learner search --keyword 重试                # 检索知识
uv run pr-learner categories                           # 分类统计
uv run pr-learner reindex                              # 重建 knowledge/index.md
uv run pr-learner convert                              # 历史 .md 转 HTML（默认只打印清单）
uv run pr-learner convert --apply                      # 真正写盘（会删原 .md，先提交或 stash）
```

> `discover` 命令**不调 LLM**，直接把 GitHub 搜索语法传给 `gh`——agent 自己就会写搜索语法。
> 页面上的「查找 PR」才需要模型把中文关键词翻成搜索语法。

> `convert` 转出来的 HTML 只有标题/列表/表格/代码块，**没有卡片、折叠和图表**——
> 那些要重新走一次「分析 PR」才有。

## 前端依赖

Vue / marked / DOMPurify / highlight.js / mermaid 全部本地打包在 `src/pr_learner/static/vendor/`，
**不走 CDN**，断网也能用。版本、SHA256 与更新方式见 [docs/vendor.md](./docs/vendor.md)。

## 测试

```bash
uv run --group dev pytest -q
```

更多命令与架构见 [CLAUDE.md](./CLAUDE.md)、[AGENTS.md](./AGENTS.md)；交接状态见 [docs/handoff.md](./docs/handoff.md)。
