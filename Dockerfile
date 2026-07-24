# 使用 uv 官方镜像，多阶段构建保持镜像精简
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# 先装依赖，利用层缓存。README.md 被 pyproject 的 readme 字段引用，构建包时需要
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

# 知识库通过卷挂载进来
ENV PR_LEARNER_KNOWLEDGE_DIR=/data/knowledge

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "pr_learner.api:app", "--host", "0.0.0.0", "--port", "8000"]
