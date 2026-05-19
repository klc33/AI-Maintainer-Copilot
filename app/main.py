# app/main.py
import sys
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI

from app.infra.vault import vault, VaultError
from app.infra.db import engine, Base
from app.api.error_handlers import register_exception_handlers

logger = structlog.get_logger()

async def check_vault():
    if not vault.health():
        raise VaultError("Vault is unreachable or sealed.")
    logger.info("Vault healthy")

async def check_db_migration():
    from sqlalchemy import text
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current = res.scalar()
        expected = "001"
        if current != expected:
            raise VaultError(f"DB migration not at head. Current: {current}, expected: {expected}")
    logger.info("Database migration at head")

async def perform_boot_checks():
    try:
        await check_vault()
        await check_db_migration()
        logger.info("All boot checks passed")
    except Exception as e:
        logger.critical("Boot check failed", error=str(e))
        sys.exit(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await perform_boot_checks()
    yield
    await engine.dispose()

app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)
register_exception_handlers(app)

@app.get("/health")
async def health():
    return {"status": "ok"}