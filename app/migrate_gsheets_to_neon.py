# app/migrate_gsheets_to_neon.py
# One-shot migration from Google Sheets -> Neon/PostgreSQL
# Env:
#   DATABASE_URL=postgresql://USER:PASS@HOST.neon.tech:5432/DB?sslmode=require
#   GOOGLE_SHEETS_ID=<your_sheet_id>
#   GOOGLE_CREDENTIALS_JSON=<full service account JSON in one line>  (or)
#   GOOGLE_CREDENTIALS_FILE=/app/credentials.json

import os
import re
import json
import ssl
import asyncio
from typing import Dict, Any, List, Optional
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


# ---------- helpers ----------

def _s(v) -> Optional[str]:
    """
    Safely cast to str, strip spaces, return None if empty/None.
    Works if v is int/float/etc.
    """
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _digits(v) -> Optional[str]:
    """Keep only digits from value (for phone-like fields)."""
    s = _s(v)
    if not s:
        return None
    d = re.sub(r"\D+", "", s)
    return d or None


def _parse_dt(s: Any) -> Optional[datetime]:
    """Try multiple datetime formats; return None if unparseable."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    # ISO 8601 fallback
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_gspread_client() -> gspread.Client:
    """
    Prefer GOOGLE_CREDENTIALS_JSON (inline), fallback to GOOGLE_CREDENTIALS_FILE.
    """
    json_inline = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if json_inline:
        info = json.loads(json_inline)
        creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    elif json_file and os.path.isfile(json_file):
        creds = Credentials.from_service_account_file(json_file, scopes=SCOPE)
    else:
        raise RuntimeError("Provide GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE for Google Sheets auth")
    return gspread.authorize(creds)


async def _get_pool() -> asyncpg.Pool:
    dsn = os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL") or os.getenv("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL (or NEON_DB_URL/SUPABASE_DB_URL) is not set")
    ssl_ctx = ssl.create_default_context()
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=60, ssl=ssl_ctx)


def _rows(ws) -> List[Dict[str, Any]]:
    """
    Read all records from a worksheet.
    Lowercase and trim header keys; keep raw values (numbers/strings).
    """
    records = ws.get_all_records()  # no numeric_grid here for max compatibility
    norm = []
    for r in records:
        norm.append({(k or "").strip().lower(): v for k, v in r.items()})
    return norm


# ---------- upserts ----------

async def upsert_clients(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        # accept user_id from different headers
        uid_raw = r.get("user_id") or r.get("id") or r.get("tg_id")
        if uid_raw is None:
            continue
        # robust int parsing (strip non-digits)
        digits = _digits(uid_raw)
        if not digits:
            continue
        try:
            user_id = int(digits)
        except Exception:
            continue

        data.append((
            user_id,
            _s(r.get("username")),
            _s(r.get("full_name") or r.get("name")),
            _s(r.get("phone")),
            _parse_dt(r.get("created_at")),
            _parse_dt(r.get("updated_at")),
        ))
    if not data:
        return

    await con.executemany("""
        insert into clients(user_id, username, full_name, phone, created_at, updated_at)
        values($1,$2,$3,$4, coalesce($5, now()), coalesce($6, now()))
        on conflict (user_id) do update set
          username = excluded.username,
          full_name = excluded.full_name,
          phone = excluded.phone,
          updated_at = coalesce(excluded.updated_at, now());
    """, data)


async def upsert_addresses(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    patch_username = []
    for r in rows:
        digits = _digits(r.get("user_id"))
        if not digits:
            continue
        try:
            user_id = int(digits)
        except Exception:
            continue

        uname = _s(r.get("username"))
        if uname:
            patch_username.append((user_id, uname))

        data.append((
            user_id,
            _s(r.get("full_name")),
            _s(r.get("phone")),
            _s(r.get("city")),
            _s(r.get("address")),
            _s(r.get("postcode")),
            _parse_dt(r.get("created_at")),
            _parse_dt(r.get("updated_at")),
        ))
    if data:
        await con.executemany("""
            insert into addresses(user_id, full_name, phone, city, address, postcode, created_at, updated_at)
            values($1,$2,$3,$4,$5,$6, coalesce($7, now()), coalesce($8, now()))
        """, data)

    # If username came only on addresses sheet, update clients table as best-effort
    if patch_username:
        await con.executemany("""
            update clients set username=$2, updated_at=now()
            where user_id=$1 and (username is distinct from $2)
        """, patch_username)


async def upsert_orders(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        oid = _s(r.get("order_id"))
        if not oid:
            continue
        data.append((
            oid,
            _s(r.get("client_name")),
            _s(r.get("phone")),
            _s(r.get("origin")),
            _s(r.get("status")),
            _s(r.get("note")),
            _s(r.get("country")),
            _parse_dt(r.get("created_at")),
            _parse_dt(r.get("updated_at")),
        ))
    if not data:
        return

    await con.executemany("""
        insert into orders(order_id, client_name, phone, origin, status, note, country, created_at, updated_at)
        values($1,$2,$3,$4,$5,$6,$7, coalesce($8, now()), coalesce($9, now()))
        on conflict (order_id) do update set
          client_name = excluded.client_name,
          phone = excluded.phone,
          origin = excluded.origin,
          status = excluded.status,
          note = excluded.note,
          country = excluded.country,
          updated_at = coalesce(excluded.updated_at, now());
    """, data)


async def upsert_participants(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        oid = _s(r.get("order_id"))
        username = _s(r.get("username"))
        if not oid or not username:
            continue
        data.append((oid, username, _parse_dt(r.get("created_at"))))
    if not data:
        return

    await con.executemany("""
        insert into participants(order_id, username, created_at)
        values($1,$2, coalesce($3, now()))
        on conflict (order_id, username) do nothing;
    """, data)


async def upsert_subscriptions(con: asyncpg.Connection, rows: List[Dict[str, Any]]):
    data = []
    for r in rows:
        digits = _digits(r.get("user_id"))
        if not digits:
            continue
        try:
            user_id = int(digits)
        except Exception:
            continue
        oid = _s(r.get("order_id"))
        if not oid:
            continue
        data.append((
            user_id,
            oid,
            _s(r.get("last_sent_status")),
            _parse_dt(r.get("created_at")),
            _parse_dt(r.get("updated_at")),
        ))
    if not data:
        return

    await con.executemany("""
        insert into subscriptions(user_id, order_id, last_sent_status, created_at, updated_at)
        values($1,$2,$3, coalesce($4, now()), coalesce($5, now()))
        on conflict (user_id, order_id) do update set
          last_sent_status = excluded.last_sent_status,
          updated_at = coalesce(excluded.updated_at, now());
    """, data)


# ---------- main ----------

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
