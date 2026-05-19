# docker/model_server.Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build tools needed for blis (spaCy dependency)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# CRITICAL: Install CPU-only torch BEFORE syncing anything else
# This prevents transformers[torch] from pulling CUDA variants from PyPI
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision

# Now sync remaining dependencies - torch already installed so uv will skip it
RUN uv sync --frozen --group model-server --no-dev

# Copy the application code
COPY . .

EXPOSE 8001
CMD ["uv", "run", "uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]