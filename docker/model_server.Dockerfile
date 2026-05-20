# docker/model_server.Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install build tools + headers (needed to compile blis/thinc from source)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy only dependency files (layer is cached unless they change)
COPY pyproject.toml uv.lock ./

# Install model-server deps.
# numpy==1.26.4 is already pinned in pyproject.toml & lockfile.
# If a wheel for blis isn't available, uv sync will compile it from source
# using the build tools we installed above.
RUN uv sync --frozen --group model-server --no-dev

# Replace CPU torch with CUDA torch (cached after first build)
RUN uv pip install --system --force-reinstall --no-deps \
    torch torchvision \
    --index-url https://download.pytorch.org/whl/cu121

# Download spaCy model
RUN uv run python -m spacy download en_core_web_sm

# Copy the rest of the application
COPY . .

EXPOSE 8001
CMD ["uv", "run", "uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]