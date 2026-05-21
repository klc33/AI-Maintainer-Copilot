# docker/api.Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Pin Python to 3.12; pyproject.toml allows >=3.12 but the project is only
# validated against 3.12, and some C-extension wheels (e.g. blis used by
# the model-server group) won't build on 3.13.
ENV UV_PYTHON=3.12

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --group api --no-dev

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000
# Invoke uvicorn from the venv directly. `uv run` would implicitly re-sync
# against the default groups and remove the --group api packages.
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]