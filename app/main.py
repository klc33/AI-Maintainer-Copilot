# app/main.py
import sys
import os
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI

from app.infra.middleware import ExceptionLoggingMiddleware
from app.infra.vault import vault, VaultError
from app.infra.db import engine, Base
from app.infra.error_handlers import register_exception_handlers
from app.infra.redaction import structlog_redactor
from app.api import (
    auth_router,
    chat_router,
    memory_router,
    widget_router,
    widget_catalog_router,
    widget_admin_router,
)


# ── structlog with the redaction processor ────────────
# The redactor runs *first* so even built-in processors (timestamp,
# log_level, key sorting…) can't accidentally serialize a secret that's
# already been formatted into a string elsewhere.
structlog.configure(
    processors=[
        structlog_redactor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(structlog.stdlib.logging.INFO),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# ── Boot checks ────────────────────────────────────────
async def check_vault():
    if not vault.health():
        raise VaultError("Vault is unreachable or sealed.")
    logger.info("Vault healthy")

async def check_db_migration():
    """Verify the DB is at one of Alembic's current head revisions.

    Previously this pinned a string literal ("003") which had to be hand-
    bumped on every migration. Reading heads from the script directory
    means new migrations boot cleanly without a code edit."""
    from sqlalchemy import text
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("/app/alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())

    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current = res.scalar()
        if current not in heads:
            raise VaultError(
                f"DB migration not at head. Current: {current}, expected one of: {sorted(heads)}"
            )
    logger.info("Database migration at head", current=current)

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

# ── Middleware + routers ───────────────────────────────
app.add_middleware(ExceptionLoggingMiddleware)
app.include_router(memory_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(widget_router)
app.include_router(widget_catalog_router)
app.include_router(widget_admin_router)


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