# -*- coding: utf-8 -*-
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row
import logging
import datetime

logger = logging.getLogger(__name__)


# ---------- connection ----------

def _dsn() -> str:
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("NEON_DB_URL")
        or os.getenv("SUPABASE_DB_URL")
        or ""
    )

def _conn():
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL/NEON_DB_URL is not set")
    return psycopg.connect(dsn, row_factory=dict_row)


# ---------- helpers / DDL (idempotent) ----------

def _ensure_schema():
    with _conn() as con, con.cursor() as cur:
        # clients
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.clients (
          id          bigserial PRIMARY KEY,
          tg_id       bigint,
          username    text,
          phone       text,
          created_at  timestamptz DEFAULT now()
        );
        """)
        # addresses
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.addresses (
          id          bigserial PRIMARY KEY,
          client_id   bigint REFERENCES public.clients(id),
          city        text,
          address     text,
          receiver    text,
          phone       text,
          created_at  timestamptz DEFAULT now()
        );
        """)
        # orders (минимально необходимые поля)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.orders (
          id           bigserial PRIMARY KEY,
          order_key    text UNIQUE,        -- может быть NULL/дубликаты, но лучше уникальный
          client_id    bigint REFERENCES public.clients(id),
          address_id   bigint REFERENCES public.addresses(id),
          status       text,
          title        text,
          store        text,
          color        text,
          size         text,
          qty          numeric,
          price        numeric,
          currency     text,
          comment      text,
          created_at   timestamptz DEFAULT now(),
          updated_at   timestamptz
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS orders_created_idx ON public.orders(created_at);")
        # Create indexes only if the corresponding columns exist to avoid errors
        def _col_exists(table: str, column: str) -> bool:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s",
                (table, column),
            )
            return cur.fetchone() is not None

        if _col_exists('orders', 'status'):
            cur.execute("CREATE INDEX IF NOT EXISTS orders_status_idx  ON public.orders(status);")
        if _col_exists('orders', 'client_id'):
            cur.execute("CREATE INDEX IF NOT EXISTS orders_client_idx  ON public.orders(client_id);")
        if _col_exists('orders', 'address_id'):
            cur.execute("CREATE INDEX IF NOT EXISTS orders_addr_idx    ON public.orders(address_id);")


# ---------- search ----------

def _like(s: str) -> str:
    return f"%{s.strip()}%" if s else "%"
def search_orders(q: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    """
    Возвращает последние заказы. Строит запрос динамически, основываясь на доступных колонках
    в таблицах public.orders, public.clients и public.addresses — это позволяет работать
    с базой, где схема отличается от ожидаемой.
    """
    _ensure_schema()
    q = (q or "").strip()
    q_like = _like(q)

    # если q — чисто число, попробуем матчер по id
    id_exact: Optional[int] = None
    if re.fullmatch(r"\d+", q or ""):
        try:
            id_exact = int(q)
        except Exception:
            id_exact = None

    # Получаем доступные колонки для таблиц
    def _get_columns(table: str) -> List[str]:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                (table,),
            )
            rows = cur.fetchall() or []
            cols: List[str] = []
            for r in rows:
                # dict_row returns mapping-like rows, otherwise use tuple access
                if hasattr(r, 'get'):
                    name = r.get('column_name')
                else:
                    try:
                        name = r[0]
                    except Exception:
                        name = None
                if name:
                    cols.append(name)
            return cols

    orders_cols = set(_get_columns('orders'))
    clients_cols = set(_get_columns('clients'))
    addresses_cols = set(_get_columns('addresses'))

    select_parts = []
    # Map of alias -> column expression and output key
    # We'll always try to include common order fields if present
    def add(col, alias=None):
        if col in orders_cols:
            select_parts.append(f"o.{col} AS {col}")

    # prefer the actual schema used in production: order_id, client_name, phone, origin, note, country
    for c in ['order_id', 'order_key', 'status', 'client_name', 'phone', 'origin', 'note', 'country', 'title', 'store', 'color', 'size', 'qty', 'price', 'currency', 'comment', 'created_at', 'updated_at']:
        add(c)

    join_clients = False
    if 'client_id' in orders_cols and ('id' in clients_cols or 'tg_id' in clients_cols):
        # We will join clients if it seems to exist
        join_clients = True
        if 'id' in clients_cols:
            select_parts.append("c.id AS client_id")
        if 'username' in clients_cols:
            select_parts.append("c.username AS client_username")
        if 'phone' in clients_cols:
            select_parts.append("c.phone AS client_phone")

    join_addresses = False
    if 'address_id' in orders_cols and ('id' in addresses_cols):
        join_addresses = True
        if 'id' in addresses_cols:
            select_parts.append("a.id AS address_id")
        if 'city' in addresses_cols:
            select_parts.append("a.city AS address_city")
        if 'address' in addresses_cols:
            select_parts.append("a.address AS address_line")
        if 'receiver' in addresses_cols:
            select_parts.append("a.receiver AS address_receiver")
        if 'phone' in addresses_cols:
            select_parts.append("a.phone AS address_phone")

    if not select_parts:
        # No columns found in orders — return empty
        return []

    select_clause = ",\n      ".join(select_parts)

    sql = f"""
    SELECT
      {select_clause}
    FROM public.orders o
    """
    if join_clients:
        # prefer joining by explicit client_id if it exists; otherwise try phone or client_name heuristics
        if 'client_id' in orders_cols and ('id' in clients_cols or 'tg_id' in clients_cols):
            sql += "LEFT JOIN public.clients c ON c.id = o.client_id\n"
        elif 'phone' in orders_cols and 'phone' in clients_cols:
            sql += "LEFT JOIN public.clients c ON c.phone = o.phone\n"
        elif 'client_name' in orders_cols and ('username' in clients_cols or 'full_name' in clients_cols):
            # join if username or full_name matches client_name
            sql += "LEFT JOIN public.clients c ON (c.username = o.client_name OR c.full_name = o.client_name)\n"
    if join_addresses:
        sql += "LEFT JOIN public.addresses a ON a.id = o.address_id\n"

    # Where clause: try to reference only existing columns
    where_clauses = []
    params = {"q": q, "q_like": q_like, "id_exact": id_exact, "limit": limit}
    # prefer exact match on order_id if user provided a likely order id
    if 'order_id' in orders_cols and q:
        where_clauses.append("o.order_id = %(q)s")
    if 'order_key' in orders_cols:
        where_clauses.append("o.order_key ILIKE %(q_like)s")
    if 'title' in orders_cols:
        where_clauses.append("o.title ILIKE %(q_like)s")
    if 'store' in orders_cols:
        where_clauses.append("o.store ILIKE %(q_like)s")
    if 'color' in orders_cols:
        where_clauses.append("o.color ILIKE %(q_like)s")
    if 'size' in orders_cols:
        where_clauses.append("o.size ILIKE %(q_like)s")
    if join_clients and 'username' in clients_cols:
        where_clauses.append("c.username ILIKE %(q_like)s")
    if join_clients and 'phone' in clients_cols:
        where_clauses.append("c.phone ILIKE %(q_like)s")
    # keep numeric id support if present
    if 'id' in orders_cols and id_exact is not None:
        where_clauses.append("o.id = %(id_exact)s")

    where_sql = "(%(q)s = '' OR " + " OR ".join(where_clauses) + ")" if where_clauses else "TRUE"

    sql += f"WHERE {where_sql}\n"
    # Build ORDER BY only with existing columns to avoid referencing missing ones
    order_by_parts: List[str] = []
    # order by most-recent timestamps available, or fallback to order_id
    if 'updated_at' in orders_cols:
        order_by_parts.append("o.updated_at DESC NULLS LAST")
    if 'created_at' in orders_cols:
        order_by_parts.append("o.created_at DESC NULLS LAST")
    # if none of timestamps exist but order_id exists, sort by order_id (lexicographically)
    if not order_by_parts and 'order_id' in orders_cols:
        order_by_parts.append("o.order_id DESC")
    if order_by_parts:
        sql += "ORDER BY " + ", ".join(order_by_parts) + "\n"

    sql += "LIMIT %(limit)s\n"

    rows: List[Any] = []
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
            try:
                logger.info("search_orders: fetched %d rows", len(rows))
                if rows:
                    # log keys of the first row to help debugging schema mapping
                    first = rows[0]
                    if hasattr(first, 'keys'):
                        logger.debug("search_orders: first row keys: %s", list(first.keys()))
                    else:
                        logger.debug("search_orders: first row sample: %s", str(first))
            except Exception:
                # never fail the query because of logging
                logger.exception("search_orders: failed to log rows")
    except Exception as e:
        logger.error("Final orders query failed: %s", e)
        return []

    out: List[Dict[str, Any]] = []
    def _to_json(v):
        # convert datetime/date to ISO strings for JSON serialization
        if isinstance(v, (datetime.datetime, datetime.date)):
            try:
                return v.isoformat()
            except Exception:
                return str(v)
        return v

    for r in rows:
        # r is dict-row (dict-like). Use .get safely.
        get = r.get if hasattr(r, 'get') else (lambda k: None)
        created = _to_json(get('created_at'))
        updated = _to_json(get('updated_at'))
        out.append(
            {
                "order_id": get('order_id') or get('order_key'),
                "status": get('status'),
                "client_name": get('client_name') or get('client_username'),
                "phone": get('phone') or get('client_phone') or get('address_phone'),
                "origin": get('origin'),
                "note": get('note') or get('comment'),
                "country": get('country'),
                "created_at": created,
                "updated_at": updated,
                "client": {
                    "id": get('client_id') or get('id'),
                    "username": get('client_username'),
                    "phone": get('client_phone'),
                },
                "address": {
                    "id": get('address_id'),
                    "city": get('address_city'),
                    "address": get('address_line'),
                    "receiver": get('address_receiver'),
                    "phone": get('address_phone'),
                },
            }
        )
    return out
