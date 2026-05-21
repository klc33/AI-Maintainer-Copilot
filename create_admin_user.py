# create_admin_user.py
"""Create or reset an admin user in the copilot `users` table.

Run via:
    docker compose exec api /app/.venv/bin/python /app/create_admin_user.py

Reads optional env:
    ADMIN_EMAIL    (default: admin@example.com)
    ADMIN_PASSWORD (default: admin123)

Earlier version wrote to the langfuse database with langfuse's column names,
which never created an app admin. This version targets the copilot DB and
hashes the password with fastapi-users' PasswordHelper so the credential is
accepted by /auth/jwt/login.
"""
import asyncio
import os
import asyncpg
from fastapi_users.password import PasswordHelper


async def main():
    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("ADMIN_PASSWORD", "admin123")

    helper = PasswordHelper()
    hashed = helper.hash(password)

    # asyncpg expects plain postgresql://, not postgresql+asyncpg://
    raw_url = os.environ["DATABASE_URL"]
    url = raw_url.replace("+asyncpg", "")

    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DELETE FROM users WHERE email = $1", email)
        await conn.execute(
            """
            INSERT INTO users (
                id, email, hashed_password,
                is_active, is_superuser, is_verified,
                role, created_at, updated_at
            )
            VALUES (
                gen_random_uuid(), $1, $2,
                true, true, true,
                'admin', now(), now()
            )
            """,
            email, hashed,
        )
        print(f"Admin user '{email}' created in copilot DB.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
