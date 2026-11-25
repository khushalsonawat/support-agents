# One image for the agent, the stand-in orders service and their one-off steps.
# The pinned claude-agent-sdk wheel carries the Claude CLI, so no Node is needed.
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /usr/local/bin/uv

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra otel && rm /usr/local/bin/uv

COPY app ./app
COPY migrations ./migrations
COPY orders_service ./orders_service

# No secrets in the image: every setting comes from the environment at run time.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin agent
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
