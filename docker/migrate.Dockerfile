# docker/migrate.Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Only the packages migrations actually need - no uv, no lock file, no CUDA risk
RUN pip install --no-cache-dir alembic "sqlalchemy[asyncio]" asyncpg

COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY app/ ./app/

ENV PYTHONPATH=/app

CMD ["python", "-m", "alembic", "upgrade", "head"]