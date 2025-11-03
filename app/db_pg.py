# db_pg.py — async Postgres helper (Neon/Supabase free tier friendly)
import os
import asyncpg
from typing import Optional, List, Dict, Any

_POOL: Optional[asyncpg.Pool] = None

async def get_pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or os.getenv("NEON_DB_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL (or SUPABASE_DB_URL / NEON_DB_URL) is not set")
        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=30)
    return _POOL

async def execute(sql: str, *args):
    pool = await get_pool()
    async with pool.acquire() as con:
        return await con.execute(sql, *args)

async def fetch(sql: str, *args):
    pool = await get_pool()
    async with pool.acquire() as con:
        rows = await con.fetch(sql, *args)
        return [dict(r) for r in rows]

async def fetchrow(sql: str, *args):
    pool = await get_pool()
    async with pool.acquire() as con:
        r = await con.fetchrow(sql, *args)
        return dict(r) if r else None

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
