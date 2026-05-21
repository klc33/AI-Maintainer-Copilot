# docker/streamlit.Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Pin Python to 3.12 (pyproject allows >=3.12 but only 3.12 is validated).
ENV UV_PYTHON=3.12

COPY pyproject.toml uv.lock ./

# Install streamlit group deps
RUN uv sync --frozen --group streamlit --no-dev

COPY . .

EXPOSE 8501

# Streamlit requires a different entry point. Invoke the venv binary
# directly so `uv run` does not implicitly re-sync the venv.
CMD ["/app/.venv/bin/streamlit", "run", "streamlit_app/Home.py", "--server.port=8501", "--server.address=0.0.0.0"]