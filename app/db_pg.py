# db_pg.py — async Postgres helper (Neon/Supabase free tier friendly)
import os
import logging
import asyncio
from typing import Optional, List, Dict, Any, Union
import asyncpg
from asyncpg.pool import Pool
from asyncpg.exceptions import PostgresError

logger = logging.getLogger(__name__)

_POOL: Optional[Pool] = None
_POOL_LOCK = asyncio.Lock()

async def get_pool() -> Pool:
    """Get or create the database connection pool with proper error handling and retry logic."""
    global _POOL
    
    if _POOL is not None:
        return _POOL
        
    async with _POOL_LOCK:  # Prevent multiple pool creations
        if _POOL is not None:  # Double-check after acquiring lock
            return _POOL
            
        dsn = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or os.getenv("NEON_DB_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL (or SUPABASE_DB_URL / NEON_DB_URL) is not set")
            
        for attempt in range(3):  # Retry logic for initial connection
            try:
                _POOL = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=5,
                    command_timeout=30,
                    max_inactive_connection_lifetime=300,  # 5 minutes
                    setup=lambda conn: conn.execute('SET statement_timeout = 30000;')  # 30 second timeout
                )
                logger.info("Database connection pool established")
                return _POOL
            except PostgresError as e:
                if attempt == 2:  # Last attempt
                    logger.error("Failed to create database pool after 3 attempts", exc_info=e)
                    raise
                logger.warning(f"Database connection attempt {attempt + 1} failed, retrying...", exc_info=e)
                await asyncio.sleep(1)

async def execute(sql: str, *args) -> str:
    """Execute a SQL command with proper error handling."""
    try:
        pool = await get_pool()
        async with pool.acquire() as con:
            return await con.execute(sql, *args)
    except PostgresError as e:
        logger.error(f"Database execution error: {e}", exc_info=e)
        raise

async def fetch(sql: str, *args) -> List[Dict[str, Any]]:
    """Fetch multiple rows with proper error handling and timeout management."""
    try:
        pool = await get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)
            return [dict(r) for r in rows]
    except asyncio.TimeoutError:
        logger.error(f"Database query timeout: {sql[:100]}...")
        raise
    except PostgresError as e:
        logger.error(f"Database fetch error: {e}", exc_info=e)
        raise

async def fetchrow(sql: str, *args) -> Optional[Dict[str, Any]]:
    """Fetch a single row with proper error handling and timeout management."""
    try:
        pool = await get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(sql, *args)
            return dict(r) if r else None
    except asyncio.TimeoutError:
        logger.error(f"Database query timeout: {sql[:100]}...")
        raise
    except PostgresError as e:
        logger.error(f"Database fetchrow error: {e}", exc_info=e)
        raise

# --- Admins ---
async def upsert_owner_from_env():
    login = (os.getenv("ADMIN_LOGIN") or "admin").strip()
    avatar= os.getenv("ADMIN_AVATAR") or ""
    role  = "owner"
    await execute(
        """
        insert into admins(login, password_hash, role, avatar)
        values($1,'', $2, $3)
        on conflict (login) do update set role=excluded.role, avatar=excluded.avatar, updated_at=now()
        """, login, role, avatar
    )

async def admin_get(login: str) -> Optional[Dict[str, Any]]:
    return await fetchrow("select login, role, avatar from admins where login=$1", login)

async def admin_set_avatar(login: str, url: str) -> bool:
    await execute("update admins set avatar=$2, updated_at=now() where login=$1", login, url)
    return True

# --- Chat ---
async def chat_list(since_id: int=0, limit:int=200) -> List[Dict[str,Any]]:
    if since_id:
        return await fetch(
            """
            select id, login, avatar, text, ref, created_at
            from chat_messages where id>$1
            order by id asc limit $2
            """, since_id, limit
        )
    else:
        rows = await fetch(
            """
            select id, login, avatar, text, ref, created_at
            from chat_messages
            order by id desc limit $1
            """, limit
        )
        rows.reverse()
        return rows

async def chat_send(login: str, avatar: str, text: str, ref: str) -> Dict[str,Any]:
    row = await fetchrow(
        """
        insert into chat_messages(login, avatar, text, ref)
        values($1,$2,$3,$4)
        returning id, created_at
        """, login, avatar, text, ref
    )
    return row or {}
