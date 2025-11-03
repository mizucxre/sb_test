"""
Migrate data from Google Sheets -> Postgres (Supabase/Neon).
Idempotent, safe to re-run. Reads tabs and upserts into tables from schema.sql
Usage:
  1) pip install -r requirements_migration.txt
  2) Fill .env (see .env.example)
  3) python migrate_sheets_to_pg.py
"""
import os, asyncio, json, re
from typing import Dict, List, Any
import asyncpg
import gspread
from google.oauth2.service_account import Credentials

def _load_gspread_client() -> gspread.Client:
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_json and not sa_file:
        raise RuntimeError("Provide GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file and not sa_json:
        with open(sa_file, "r", encoding="utf-8") as f:
            sa_json = f.read()
    info = json.loads(sa_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def _get_ws_data(gc: gspread.Client, spreadsheet_id: str, title: str):
    sh = gc.open_by_key(spreadsheet_id); ws = sh.worksheet(title)
    return ws.get_all_records()

async def get_pool() -> asyncpg.Pool:
    dsn = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or os.getenv("NEON_DB_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL (or SUPABASE_DB_URL/NEON_DB_URL) not set")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=60)

def _norm_phone(v: str) -> str:
    return re.sub(r"\D+", "", v or "")

async def upsert_admins(con: asyncpg.Connection, rows: list[dict]):
    sql = """
    insert into admins(login, password_hash, role, avatar, created_at, updated_at)
    values($1, coalesce($2,''), coalesce($3,'admin'), coalesce($4,''), now(), now())
    on conflict (login) do update set
      password_hash = excluded.password_hash,
      role          = excluded.role,
      avatar        = excluded.avatar,
      updated_at    = now()
    """
    for r in rows:
        await con.execute(sql,
            str(r.get("login","")), str(r.get("password_hash","")),
            str(r.get("role","admin")), str(r.get("avatar","")),
        )

async def upsert_chat(con: asyncpg.Connection, rows: list[dict]):
    sql_with_id = """
      insert into chat_messages(id, login, avatar, text, ref, created_at)
      values($1,$2,$3,$4,$5, coalesce($6, now()))
      on conflict (id) do nothing
    """
    sql_no_id = """
      insert into chat_messages(login, avatar, text, ref, created_at)
      values($1,$2,$3,$4, coalesce($5, now()))
      on conflict do nothing
    """
    for r in rows:
        rid = r.get("id")
        if rid not in (None, ""):
            await con.execute(sql_with_id,
                int(rid), str(r.get("login","")), str(r.get("avatar","")),
                str(r.get("text","")), str(r.get("ref","")), r.get("created_at"),
            )
        else:
            await con.execute(sql_no_id,
                str(r.get("login","")), str(r.get("avatar","")),
                str(r.get("text","")), str(r.get("ref","")), r.get("created_at"),
            )

async def main():
    SHEET = os.getenv("SPREADSHEET_ID", "").strip()
    if not SHEET: raise RuntimeError("Set SPREADSHEET_ID")
    gc = _load_gspread_client(); pool = await get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            tabs = {"admins": upsert_admins, "chat": upsert_chat}  # extend later
            for title, fn in tabs.items():
                try:
                    rows = _get_ws_data(gc, SHEET, title)
                except Exception as e:
                    print(f"[skip] '{title}': {e}"); continue
                print(f"[tab:{title}] {len(rows)} rows"); await fn(con, rows)
    await pool.close(); print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
