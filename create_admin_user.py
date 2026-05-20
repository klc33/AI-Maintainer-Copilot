# create_admin_user.py
import asyncpg, asyncio, os, bcrypt
from urllib.parse import urlparse, urlunparse

async def main():
    # Hash the password
    pw = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()

    # Parse the DATABASE_URL (postgresql+asyncpg://copilot:changeme@db:5432/copilot)
    raw_url = os.environ["DATABASE_URL"]
    # Remove the +asyncpg driver prefix to get a plain postgresql:// URL
    url = raw_url.replace("+asyncpg", "")
    parts = urlparse(url)
    # Change the database name (path) to "langfuse", keep user/pass unchanged
    new_parts = parts._replace(path="/langfuse")
    db_url = urlunparse(new_parts)

    conn = await asyncpg.connect(db_url)

    # Remove any existing admin user
    await conn.execute("DELETE FROM users WHERE email = $1", "admin@example.com")

    # Insert the new admin user
    await conn.execute("""
        INSERT INTO users (id, name, email, email_verified, password, admin, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, now(), $3, true, now(), now())
    """, "Admin", "admin@example.com", pw)

    print("Admin user created successfully.")
    await conn.close()

asyncio.run(main())