# 0004 — LLM 辅助的 PR 查找

日期：2026-07-28

## 背景

原来的入口假设用户「已经知道该看哪个 PR」——必须先自己在 GitHub 上翻到某个 PR，
才能把地址粘进「分析 PR」页。缺的是前一步：只有一个模糊的中文问题和一个仓库名，
需要先定位出该读哪些 PR。

## 决定

新增「查找 PR」页与 `discover.py`，LLM 在流程里承担两段工作，中间夹一次真实的 gh 搜索：

```
关键词（可中文）+ 仓库
  → LLM 翻成 GitHub 搜索语法        （build_query）
  → gh search prs 拿真实候选         （fetch.search_prs）
  → LLM 筛选、排序、写中文简介        （rank_prs / join_ranked）
  → 结果卡片 + 「分析此 PR」按钮接回已有的 分析 → 草稿 → review 流程
```

## 理由

- **翻译步骤不可省**：GitHub 不索引中文，中文关键词直接搜实测返回空列表。
- **筛选步骤有价值**：GitHub search 是关键词匹配，候选混入无关 PR 是常态；
  模型重排 + 一句中文简介，才能让人扫一眼就决定读哪个。
- **模型不碰事实**：number 和 url 全部来自 gh 的返回，`join_ranked` 丢弃候选里
  不存在的 number、链接只用候选的 url，模型只能重排和描述。
- **降级优先于报错**：任一 LLM 步骤失败都退回未排序的搜索结果并给一条 status 说明，
  而不是让页面空白——模型抽风一次不该让用户什么都看不到。
- CLI 的 `discover` 命令不调 LLM：agent 本身就是 LLM，自己会写搜索语法。

配套把 `analyze.py` 里的 LLM 传输层与模板读取层抽成 `llm.py` / `prompts.py`。
若让 `discover` 直接 import `analyze` 的私有函数，除了让 `analyze.py` 变成
「传输层 + 业务层」混合体，还有个更隐蔽的后果：`from ... import _call_llm` 会让现有
`monkeypatch.setattr(analyze, "_call_llm", ...)` 对 `discover` 失效——测试假装打了桩，
实际会打真实网络。

## 实测的 gh 约束

以下每条都在本机 gh 2.96.0 上实测过，直接决定了 `search_prs` 的实现，**改动前先读**。
危险的是前四条：它们 exit 0、返回空列表，看起来就像「没有相关 PR」。

| 现象 | 结果 |
|---|---|
| 整条 query 作单个 positional 参数 `"is:merged fix"` | gh 拼成 `is:"merged fix"`，**静默返回 `[]`** |
| 拆成多个 token 分别传 | 正确 |
| `--repo R` 与 query 里 `repo:X` 并存 | 两个范围 AND，**静默返回 `[]`** |
| query 含 `is:issue`（与 gh 自动追加的 `type:pr` 冲突） | 结果恒空 |
| `--state open` 与 query 里 `is:merged` 并存 | **静默返回 `[]`** |
| 中文关键词直搜 | `[]` |
| `--state merged` | exit 1 被拒，只接受 `open\|closed` |
| `--state closed` | **包含已合并的 PR**（实测 10 条里 5 条 merged） |
| `--merged=true` / `--merged=false` | 正确；这是筛已合并/未合并的唯一途径 |
| `--merged true`（空格形式） | `true` 被当成查询词，静默返回错结果 |
| `--sort best-match` | exit 1 被拒；默认即 best-match，不传时**不能加这个 flag** |
| query token 以 `-` 开头（`-label:bug`，合法排除语法） | 被当成 flag：`unknown shorthand flag: 'l'` |
| 所有 flag 在前、token 放 `--` 之后 | 正确，且 `-label:bug` 也能保留 |
| 仓库不存在/无权限 | exit 1，stderr 含 `cannot be searched` |
| 未登录 | exit 4 |
| search API 速率 | **30 次/分钟**（core 是 5000），不要加重试或分页循环 |
| `--limit` 上限 | 1..1000，但 >100 会内部翻多页多耗限额，代码收敛到 100 |

### 状态筛选的三个档

`gh` 没有单一的「状态」参数，三档各自要不同的 flag 组合（见 `fetch._STATE_FLAGS`）：

| 页面选项 | gh flags | 为什么 |
|---|---|---|
| 仅已合并 | `--merged=true` | `--state merged` 会被拒 |
| 仅未关闭 | `--state open` | — |
| 已关闭未合并 | `--state closed --merged=false` | 只给 `--state closed` 会混进 merged |

用户显式选了状态时，`search_prs` 会剥掉 query 里的 `is:merged`/`is:open`/`state:` 等状态限定符
（见 `_STATE_PREFIXES`）：两者矛盾时 gh 静默返空，而下拉框是更明确的意图。
相应地，`query_system.md` 也要求模型**不要**输出状态限定符——状态归下拉框管，模型只翻译主题关键词。

另外两点语义约束，写进了提示词与页面 hint：
GitHub search 只索引 PR 的 title/body/comments，**不索引 diff**（「哪个 PR 改了某函数」
是 `gh search code` 的活）；`state` 字段取值域是 `open/closed/merged` 三值，展示层别按二值处理。

## 影响

- 新增 `discover.py`、`llm.py`、`prompts.py`；`analyze.py` 变薄并保留兼容别名，
  `tests/test_analyze.py` 零改动。
- 提示词模板改子目录 `prompt_templates/{analyze,discover}/`，
  `pyproject.toml` 的 `artifacts` 同步改成 `prompt_templates/**/*`。
- 新增端点 `POST /api/discover/prs` 与 `/stream`，CLI 新增 `discover` 命令。
- 前端 `consumeStream` 改为接收 handler 回调，两个流式 tab 状态完全隔离；
  LLM 凭证抽成共享的 `this.llm`，**刻意不落 localStorage**（延续 0003 的「不落盘」承诺）。
