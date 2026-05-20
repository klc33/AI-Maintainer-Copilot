# app/main.py
import sys
import os
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI

from app.api.memory import router as memory_router
from app.api.middleware import ExceptionLoggingMiddleware
from app.infra.vault import vault, VaultError
from app.infra.db import engine, Base
from app.api.error_handlers import register_exception_handlers
from app.api.chat import router as chat_router
from app.api.widget import router as widget_router


logger = structlog.get_logger()

# ── Boot checks ────────────────────────────────────────
async def check_vault():
    if not vault.health():
        raise VaultError("Vault is unreachable or sealed.")
    logger.info("Vault healthy")

async def check_db_migration():
    from sqlalchemy import text
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current = res.scalar()
        expected = "003"   # latest migration
        if current != expected:
            raise VaultError(f"DB migration not at head. Current: {current}, expected: {expected}")
    logger.info("Database migration at head")

async def check_jwt_secret():
    """Load JWT signing key from Vault and inject it into the auth module."""
    try:
        secret_data = vault.load("secret/shared/jwt")
        if not secret_data or "secret" not in secret_data:
            raise VaultError("JWT secret not found in Vault at secret/shared/jwt")
        # Set the global in auth service
        import app.services.auth as auth_mod
        auth_mod.JWT_SECRET = secret_data["secret"]
        logger.info("JWT secret loaded from Vault")
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"Failed to load JWT secret: {e}")

async def perform_boot_checks():
    """Run all boot checks; any failure -> SystemExit."""
    try:
        await check_vault()
        await check_db_migration()
        await check_jwt_secret()
        await check_groq_key()
        await check_langfuse_keys()
        # Future checks: model server, Langfuse, eval thresholds, prompts...
        logger.info("All boot checks passed")
    except Exception as e:
        logger.critical("Boot check failed", error=str(e))
        sys.exit(1)

# ── Lifespan ───────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await perform_boot_checks()
    yield
    await engine.dispose()

app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)

# ── Exception handlers ─────────────────────────────────
register_exception_handlers(app)

# ── Health endpoint ────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}

# ── Auth router ────────────────────────────────────────
from app.api.auth import router as auth_router
app.add_middleware(ExceptionLoggingMiddleware)
app.include_router(memory_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(widget_router)




# Future routers will be added here (chat, widget, etc.)
async def check_groq_key():
    try:
        groq_data = vault.load("secret/shared/groq")
        if not groq_data or "secret" not in groq_data:
            raise VaultError("Groq API key not found in Vault at secret/shared/groq")
        os.environ["GROQ_API_KEY"] = groq_data["secret"]
        logger.info("Groq API key loaded from Vault")
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"Failed to load Groq API key: {e}")


async def check_langfuse_keys():
    try:
        lf_data = vault.load("secret/shared/langfuse")
        if not lf_data or "public" not in lf_data or "secret" not in lf_data:
            raise VaultError("Langfuse keys not found in Vault at secret/shared/langfuse")
        os.environ["LANGFUSE_PUBLIC_KEY"] = lf_data["public"]
        os.environ["LANGFUSE_SECRET_KEY"] = lf_data["secret"]
        logger.info("Langfuse keys loaded from Vault")
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"Failed to load Langfuse keys: {e}")