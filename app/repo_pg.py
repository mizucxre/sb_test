# -*- coding: utf-8 -*-
"""repo_pg.py — synchronous Postgres storage with the SAME function names as sheets.py
Drop this file into app/repo_pg.py and then in main.py replace `import sheets as sheets`
with `from . import storage as sheets` (see storage.py in instructions).
Requires: psycopg[binary] >= 3.2, python-dotenv (optional)
"""
from __future__ import annotations
import os
import re
from typing import List, Dict, Any, Optional, Tuple
import psycopg
from psycopg.rows import dict_row

_DB_URL = os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL") or os.getenv("SUPABASE_DB_URL")
if not _DB_URL:
    raise RuntimeError("DATABASE_URL (or NEON_DB_URL/SUPABASE_DB_URL) is not set")

def _conn():
    # sslmode=require should be in the URL for Neon. psycopg v3 understands it.
    return psycopg.connect(_DB_URL, row_factory=dict_row)

def _normalize_username(u: str) -> str:
    u = (u or "").strip()
    if u.startswith("@"):
        u = u[1:]
    return u.lower()

def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

# === Orders ===

def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            select * from orders where lower(order_id) = lower(%s)
        """, (order_id, ))
        return cur.fetchone()

def add_order(order: Dict[str, Any]) -> None:
    order = dict(order or {})
    oid = order.get("order_id")
    if not oid:
        raise ValueError("order_id required")
    cols = ["order_id","client_name","phone","origin","status","note","country"]
    vals = [order.get(c) for c in cols]
    with _conn() as con, con.cursor() as cur:
        cur.execute("""            insert into orders(order_id, client_name, phone, origin, status, note, country)
            values(%s,%s,%s,%s,%s,%s,%s)
            on conflict (order_id) do update set
              client_name = excluded.client_name,
              phone = excluded.phone,
              origin = excluded.origin,
              status = excluded.status,
              note = excluded.note,
              country = excluded.country,
              updated_at = now()
        """, vals)
        con.commit()

def update_order_status(order_id: str, status: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          update orders set status=%s, updated_at=now() where lower(order_id)=lower(%s)
        """, (status, order_id))
        con.commit()

def list_recent_orders(limit:int=50) -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select * from orders order by updated_at desc nulls last, created_at desc nulls last limit %s
        """, (limit,))
        return cur.fetchall() or []

def list_orders_by_status(status: str, limit:int=200) -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select * from orders where status=%s order by updated_at desc nulls last limit %s
        """, (status, limit))
        return cur.fetchall() or []

# === Participants / Clients / Subscriptions ===

def ensure_clients_from_usernames(usernames: List[str]) -> None:
    usernames = [u for u in map(_normalize_username, usernames) if u]
    if not usernames:
        return
    with _conn() as con, con.cursor() as cur:
        for u in usernames:
            cur.execute("""
              insert into clients(user_id, username)
              values(gen_random_uuid()::text::bigint, %s)
              on conflict (user_id) do nothing
            """, (u,))
        con.commit()

def ensure_participants(order_id: str, usernames: List[str]) -> None:
    usernames = [u for u in map(_normalize_username, usernames) if u]
    if not usernames:
        return
    with _conn() as con, con.cursor() as cur:
        for u in usernames:
            cur.execute("""
              insert into participants(order_id, username)
              values(%s,%s)
              on conflict (order_id, username) do nothing
            """, (order_id, u))
        con.commit()

def get_participants(order_id: str) -> List[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select username from participants where lower(order_id)=lower(%s) order by created_at asc
        """, (order_id,))
        return [r["username"] for r in cur.fetchall() or []]

def orders_for_username(username: str) -> List[Dict[str,Any]]:
    u = _normalize_username(username)
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select o.*
          from orders o
          join participants p on p.order_id = o.order_id
          where lower(p.username)=lower(%s)
          order by o.updated_at desc nulls last, o.created_at desc nulls last
        """, (u,))
        return cur.fetchall() or []

def get_orders_by_username(username: str) -> List[Dict[str,Any]]:
    return orders_for_username(username)

def get_orders_by_phone(phone: str) -> List[Dict[str,Any]]:
    d = _digits_only(phone)
    if not d:
        return []
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select * from orders where regexp_replace(coalesce(phone,''),'\D','','g') = %s
          order by updated_at desc nulls last
        """, (d,))
        return cur.fetchall() or []

def subscribe(user_id:int, order_id:str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          insert into subscriptions(user_id, order_id) values(%s,%s)
          on conflict (user_id, order_id) do nothing
        """, (user_id, order_id))
        con.commit()

def unsubscribe(user_id:int, order_id:str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          delete from subscriptions where user_id=%s and lower(order_id)=lower(%s)
        """, (user_id, order_id))
        con.commit()

def is_subscribed(user_id:int, order_id:str) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select 1 from subscriptions where user_id=%s and lower(order_id)=lower(%s)
        """, (user_id, order_id))
        return cur.fetchone() is not None

def list_subscriptions(user_id:int) -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select s.order_id, o.status, s.created_at, s.updated_at
          from subscriptions s left join orders o on o.order_id=s.order_id
          where s.user_id=%s
          order by s.created_at desc
        """, (user_id,))
        return cur.fetchall() or []

def get_all_subscriptions() -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select user_id, order_id, last_sent_status from subscriptions
        """)
        return cur.fetchall() or []

def set_last_sent_status(user_id:int, order_id:str, status:str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          update subscriptions set last_sent_status=%s, updated_at=now()
          where user_id=%s and lower(order_id)=lower(%s)
        """, (status, user_id, order_id))
        con.commit()

# === Addresses / Clients ===

def upsert_client(user_id:int, username:str=None, full_name:str=None, phone:str=None, **kwargs):
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          insert into clients(user_id, username, full_name, phone)
          values(%s,%s,%s,%s)
          on conflict (user_id) do update set
            username=excluded.username,
            full_name=excluded.full_name,
            phone=excluded.phone,
            updated_at=now()
        """, (user_id, (username or None), (full_name or None), (phone or None)))
        con.commit()

def upsert_address(user_id:int, username:str, full_name:str, phone:str, city:str, address:str, postcode:str) -> bool:
    upsert_client(user_id=user_id, username=username, full_name=full_name, phone=phone)
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          insert into addresses(user_id, full_name, phone, city, address, postcode)
          values(%s,%s,%s,%s,%s,%s)
        """, (user_id, full_name or None, phone or None, city or None, address or None, postcode or None))
        con.commit()
        return True

def list_addresses(user_id:int) -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select id, full_name, phone, city, address, postcode, created_at, updated_at
          from addresses where user_id=%s order by created_at desc
        """, (user_id,))
        return cur.fetchall() or []

def delete_address(user_id:int, address_text:str) -> bool:
    # delete by exact address text match; adjust if you store IDs on the UI
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          delete from addresses where user_id=%s and address=%s
        """, (user_id, address_text))
        con.commit()
        return cur.rowcount > 0

def get_addresses_by_usernames(usernames: List[str]) -> List[Dict[str,Any]]:
    usernames = [u for u in map(_normalize_username, usernames) if u]
    if not usernames:
        return []
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select a.*, c.username
          from addresses a join clients c on c.user_id=a.user_id
          where lower(c.username) = any(%s)
        """, (usernames,))
        return cur.fetchall() or []

def get_user_ids_by_usernames(usernames: List[str]) -> List[int]:
    usernames = [u for u in map(_normalize_username, usernames) if u]
    if not usernames:
        return []
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select user_id from clients where lower(username) = any(%s)
        """, (usernames,))
        return [r["user_id"] for r in cur.fetchall() or []]

def search_clients(q:str, limit:int=50) -> List[Dict[str,Any]]:
    q = (q or "").strip().lower()
    if not q:
        return []
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select * from clients
          where lower(coalesce(username,'')) like %s
             or lower(coalesce(full_name,'')) like %s
             or regexp_replace(coalesce(phone,''),'\D','','g') like %s
          order by updated_at desc nulls last
          limit %s
        """, (f"%{q}%", f"%{q}%", f"%{_digits_only(q)}%", limit))
        return cur.fetchall() or []

def list_clients(limit:int=200) -> List[Dict[str,Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
          select * from clients order by updated_at desc nulls last, created_at desc nulls last limit %s
        """, (limit,))
        return cur.fetchall() or []