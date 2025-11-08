# migrate_gsheets_to_neon.py — One-shot migration from Google Sheets to Neon/Postgres
# Usage:
#   1) pip install asyncpg gspread google-auth pandas python-dotenv
#   2) Fill .env (see .env.example below)
#   3) python migrate_gsheets_to_neon.py

import os
import json
import asyncio
import ssl
from typing import Dict, Any, List
from datetime import datetime

import asyncpg
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except Exception:
            pass
    try:
        # Try ISO 8601
        return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        return None

def _load_gspread_client() -> gspread.Client:
    # Prefer explicit GOOGLE_CREDENTIALS_JSON (full JSON string)
    json_inline = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if json_inline:
        info = json.loads(json_inline)
        creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    elif json_file and os.path.isfile(json_file):
        creds = Credentials.from_service_account_file(json_file, scopes=SCOPE)
    else:
        raise RuntimeError("Provide GOOGLE_CREDENTIALS_JSON (inline) or GOOGLE_CREDENTIALS_FILE path")
    return gspread.authorize(creds)

async def _get_pool() -> asyncpg.Pool:
    dsn = os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL") or os.getenv("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL (or NEON_DB_URL/SUPABASE_DB_URL) is not set")
    ssl_ctx = ssl.create_default_context()
    # Neon requires TLS; sslmode=require is OK too, but we pass an SSL context explicitly.
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=60, ssl=ssl_ctx)

def _rows(ws) -> List[Dict[str, Any]]:
    # gspread returns list of dicts already normalized by header
    rows = ws.get_all_records(numeric_grid=False)  # keep strings as is
    # strip keys and normalize case
    norm = []
    for r in rows:
        norm.append({(k or "").strip().lower(): v for k, v in r.items()})
    return norm

async def upsert_clients(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        user_id = r.get("user_id") or r.get("id") or r.get("tg_id")
        if not user_id:
            continue
        try:
            user_id = int(str(user_id).strip())
        except Exception:
            continue
        data.append((
            user_id,
            (r.get("username") or "").strip() or None,
            (r.get("full_name") or r.get("name") or "").strip() or None,
            (r.get("phone") or "").strip() or None,
            _parse_dt(str(r.get("created_at") or "")),
            _parse_dt(str(r.get("updated_at") or "")),
        ))
    if not data:
        return
    await con.executemany("""        insert into clients(user_id, username, full_name, phone, created_at, updated_at)
        values($1,$2,$3,$4, coalesce($5, now()), coalesce($6, now()))
        on conflict (user_id) do update set
          username = excluded.username,
          full_name = excluded.full_name,
          phone = excluded.phone,
          updated_at = excluded.updated_at;
    """, data)

async def upsert_addresses(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        try:
            user_id = int(str(r.get("user_id") or "").strip())
        except Exception:
            continue
        data.append((
            user_id,
            (r.get("full_name") or "").strip() or None,
            (r.get("phone") or "").strip() or None,
            (r.get("city") or "").strip() or None,
            (r.get("address") or "").strip() or None,
            (r.get("postcode") or "").strip() or None,
            _parse_dt(str(r.get("created_at") or "")),
            _parse_dt(str(r.get("updated_at") or "")),
            (r.get("username") or "").strip() or None,
        ))
    if not data:
        return
    await con.executemany("""        insert into addresses(user_id, full_name, phone, city, address, postcode, created_at, updated_at)
        values($1,$2,$3,$4,$5,$6, coalesce($7, now()), coalesce($8, now()))
    """, [(a,b,c,d,e,f,g,h) for (a,b,c,d,e,f,g,h,_) in data])
    # also ensure clients table has username if it was carried on the addresses sheet
    patch = [(u, name) for (u,_,_,_,_,_,_,_,name) in data if name]
    if patch:
        await con.executemany("""          update clients set username=$2 where user_id=$1
        """, patch)

async def upsert_orders(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        oid = (r.get("order_id") or "").strip()
        if not oid:
            continue
        data.append((
            oid,
            (r.get("client_name") or "").strip() or None,
            (r.get("phone") or "").strip() or None,
            (r.get("origin") or "").strip() or None,
            (r.get("status") or "").strip() or None,
            (r.get("note") or "").strip() or None,
            (r.get("country") or "").strip() or None,
            _parse_dt(str(r.get("created_at") or "")),
            _parse_dt(str(r.get("updated_at") or "")),
        ))
    if not data:
        return
    await con.executemany("""        insert into orders(order_id, client_name, phone, origin, status, note, country, created_at, updated_at)
        values($1,$2,$3,$4,$5,$6,$7, coalesce($8, now()), coalesce($9, now()))
        on conflict (order_id) do update set
          client_name = excluded.client_name,
          phone = excluded.phone,
          origin = excluded.origin,
          status = excluded.status,
          note = excluded.note,
          country = excluded.country,
          updated_at = excluded.updated_at;
    """, data)

async def upsert_participants(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        oid = (r.get("order_id") or "").strip()
        username = (r.get("username") or "").strip()
        if not oid or not username:
            continue
        data.append((oid, username, _parse_dt(str(r.get("created_at") or ""))))
    if not data:
        return
    await con.executemany("""        insert into participants(order_id, username, created_at)
        values($1,$2, coalesce($3, now()))
        on conflict (order_id, username) do nothing;
    """, data)

async def upsert_subscriptions(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        try:
            user_id = int(str(r.get("user_id") or "").strip())
        except Exception:
            continue
        oid = (r.get("order_id") or "").strip()
        if not oid:
            continue
        data.append((user_id, oid, (r.get("last_sent_status") or "").strip() or None,
                     _parse_dt(str(r.get("created_at") or "")),
                     _parse_dt(str(r.get("updated_at") or ""))))
    if not data:
        return
    await con.executemany("""        insert into subscriptions(user_id, order_id, last_sent_status, created_at, updated_at)
        values($1,$2,$3, coalesce($4, now()), coalesce($5, now()))
        on conflict (user_id, order_id) do update set
          last_sent_status = excluded.last_sent_status,
          updated_at = excluded.updated_at;
    """, data)

async def main():
    SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
    if not SHEET_ID:
        raise RuntimeError("GOOGLE_SHEETS_ID is not set")
    gc = _load_gspread_client()
    sh = gc.open_by_key(SHEET_ID)

    tabs = {
        "clients": upsert_clients,
        "addresses": upsert_addresses,
        "orders": upsert_orders,
        "participants": upsert_participants,
        "subscriptions": upsert_subscriptions,
    }

    pool = await _get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            for title, fn in tabs.items():
                try:
                    ws = sh.worksheet(title)
                except Exception as e:
                    print(f"[skip] '{title}': {e}")
                    continue
                rows = _rows(ws)
                print(f"[{title}] {len(rows)} rows")
                await fn(con, rows)

    await pool.close()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())