# -*- coding: utf-8 -*-
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row
import logging

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
    Возвращает последние заказы. Если q задан — ищем по id/order_key/username/phone/title/store/color/size.
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

    sql = """
    SELECT
      o.id,
      o.order_key,
      o.status,
      o.title,
      o.store,
      o.color,
      o.size,
      o.qty,
      o.price,
      o.currency,
      o.comment,
      o.created_at,
      c.id        AS client_id,
      c.username  AS client_username,
      c.phone     AS client_phone,
      a.id        AS address_id,
      a.city      AS address_city,
      a.address   AS address_line,
      a.receiver  AS address_receiver,
      a.phone     AS address_phone
    FROM public.orders o
    LEFT JOIN public.clients   c ON c.id = o.client_id
    LEFT JOIN public.addresses a ON a.id = o.address_id
    WHERE
      (%(q)s = '' OR
       o.order_key ILIKE %(q_like)s OR
       o.title     ILIKE %(q_like)s OR
       o.store     ILIKE %(q_like)s OR
       o.color     ILIKE %(q_like)s OR
       o.size      ILIKE %(q_like)s OR
       c.username  ILIKE %(q_like)s OR
       c.phone     ILIKE %(q_like)s OR
       (%(id_exact)s IS NOT NULL AND o.id = %(id_exact)s)
      )
    ORDER BY o.created_at DESC NULLS LAST, o.id DESC
    LIMIT %(limit)s
    """
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
                return [r[0] for r in cur.fetchall() or []]

        orders_cols = set(_get_columns('orders'))
        clients_cols = set(_get_columns('clients'))
        addresses_cols = set(_get_columns('addresses'))

        select_parts = []
        # Map of alias -> column expression and output key
        # We'll always try to include common order fields if present
        def add(col, alias=None):
            if col in orders_cols:
                select_parts.append(f"o.{col} AS {col}")

        for c in ['id', 'order_key', 'status', 'title', 'store', 'color', 'size', 'qty', 'price', 'currency', 'comment', 'created_at']:
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
            sql += "LEFT JOIN public.clients c ON c.id = o.client_id\n"
        if join_addresses:
            sql += "LEFT JOIN public.addresses a ON a.id = o.address_id\n"

        # Where clause: try to reference only existing columns
        where_clauses = []
        params = {"q": q, "q_like": q_like, "id_exact": id_exact, "limit": limit}
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
        if 'id' in orders_cols and id_exact is not None:
            where_clauses.append("o.id = %(id_exact)s")

        where_sql = "(%(q)s = '' OR " + " OR ".join(where_clauses) + ")" if where_clauses else "TRUE"

        sql += f"WHERE {where_sql}\nORDER BY ";
        if 'created_at' in orders_cols:
            sql += "o.created_at DESC NULLS LAST, o.id DESC\n"
        elif 'id' in orders_cols:
            sql += "o.id DESC\n"
        else:
            sql += "1=1\n"

        sql += "LIMIT %(limit)s\n"

        rows: List[Any] = []
        try:
            with _conn() as con, con.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() or []
        except Exception as e:
            logger.error("Final orders query failed: %s", e)
            return []

        out: List[Dict[str, Any]] = []
        for r in rows:
            # r is dict-row (dict-like). Use .get safely.
            get = r.get if hasattr(r, 'get') else (lambda k: None)
            out.append(
                {
                    "id": get('id'),
                    "order_key": get('order_key'),
                    "status": get('status'),
                    "title": get('title'),
                    "store": get('store'),
                    "color": get('color'),
                    "size": get('size'),
                    "qty": get('qty'),
                    "price": get('price'),
                    "currency": get('currency'),
                    "comment": get('comment'),
                    "created_at": get('created_at'),
                    "client": {
                        "id": get('client_id'),
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
