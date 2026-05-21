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

# Pin Python to 3.12. pyproject.toml says `requires-python = ">=3.12"` which
# also permits 3.13, and uv now prefers the newest match — but blis 0.7.11
# (a thinc/spaCy dep) was Cython-built against CPython 3.12 internals and
# fails to compile on 3.13.
ENV UV_PYTHON=3.12

# Copy only dependency files (layer is cached unless they change)
COPY pyproject.toml uv.lock ./

# Install model-server deps.
# numpy==1.26.4 is already pinned in pyproject.toml & lockfile.
# If a wheel for blis isn't available, uv sync will compile it from source
# using the build tools we installed above.
RUN uv sync --frozen --group model-server --no-dev

# Note: a previous `uv pip install --system ... torch --index-url cu121` line
# was removed here. It targeted the uv-managed system Python (now marked
# externally-managed) instead of /app/.venv, and used --no-deps, so it was
# both broken and inert. The CPU torch wheel from pyproject's pytorch-cpu
# index is what the venv actually uses, and compose does not request a GPU.

# Install the spaCy model directly from its release wheel. `spacy download`
# shells out to pip, which is not present in this uv-managed venv. The wheel
# URL is the same one spaCy's CLI would resolve, but using `uv pip install`
# avoids needing pip in the runtime image and also keeps uv from triggering
# an implicit project resync (which would drop --group model-server packages
# and upgrade numpy past 1.26.4, breaking thinc's C ABI).
RUN uv pip install --python /app/.venv/bin/python \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# Copy the rest of the application
COPY . .

EXPOSE 8001
# Invoke uvicorn from the venv directly. Using `uv run` here would trigger
# an implicit `uv sync` against the default groups, removing the
# model-server packages installed at build time.
CMD ["/app/.venv/bin/uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]