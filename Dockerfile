# Multi-stage build: resolve deps with uv, ship a final image without uv.

# Builder: install locked deps into /app/.venv
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# Final: must match the builder's Python version so the venv paths line up
FROM python:3.12-slim-trixie

RUN groupadd --system app && \
    useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Copy only the runtime artifacts (virtualenv + source)
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

USER app

# Script is the entrypoint, so runtime flags (e.g. --validate) append cleanly
ENTRYPOINT ["python", "src/ddns_updater.py"]

ARG VERSION=2.3.0
LABEL description="Cloudflare DDNS Updater" \
      version="${VERSION}"
