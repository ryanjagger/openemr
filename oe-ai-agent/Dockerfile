FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY src ./src
RUN test -d src/oe_ai_agent/tools && test -f src/oe_ai_agent/tools/__init__.py \
    || (echo 'BUILD ERROR: oe_ai_agent.tools missing from build context' \
        && ls -laR src/oe_ai_agent/ && exit 1)
RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "oe_ai_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
