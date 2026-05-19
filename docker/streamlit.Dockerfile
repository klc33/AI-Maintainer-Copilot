# docker/streamlit.Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

# Install streamlit group deps
RUN uv sync --frozen --group streamlit --no-dev

COPY . .

EXPOSE 8501

# Streamlit requires a different entry point
CMD ["uv", "run", "streamlit", "run", "streamlit_app/Home.py", "--server.port=8501", "--server.address=0.0.0.0"]