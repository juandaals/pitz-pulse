FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /bin/uv

WORKDIR /app

# Dependencies first so this layer stays cached while only application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY prompts/ prompts/
RUN uv sync --frozen --no-dev

RUN useradd -m app && mkdir -p /data && chown app:app /data

ENV APP_ROOT=/app \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 8000

CMD ["uvicorn", "--factory", "pitz_pulse.api:create_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
