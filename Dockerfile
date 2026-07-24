# 使用 uv 官方镜像，多阶段构建保持镜像精简
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# 安装 GitHub CLI：fetch.py 依赖 gh 拉取 PR。用官方 apt 源，pin 到镜像自带版本。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用层缓存。README.md 被 pyproject 的 readme 字段引用，构建包时需要
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

# 知识库通过卷挂载进来
ENV PR_LEARNER_KNOWLEDGE_DIR=/data/knowledge

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "pr_learner.api:app", "--host", "0.0.0.0", "--port", "8000"]
