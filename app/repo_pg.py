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
    # Try the full query with joins. If client's/address schema differs, fall back to orders-only query.
    rows = []
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(sql, {"q": q, "q_like": q_like, "id_exact": id_exact, "limit": limit})
            rows = cur.fetchall() or []
    except Exception as e:
        # Likely schema mismatch (missing client/address columns). Log and try fallback.
        logger.warning("Full orders query failed, falling back to orders-only query: %s", e)
        fallback_sql = """
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
          o.created_at
        FROM public.orders o
        WHERE (%(q)s = '' OR
               o.order_key ILIKE %(q_like)s OR
               o.title     ILIKE %(q_like)s OR
               o.store     ILIKE %(q_like)s OR
               o.color     ILIKE %(q_like)s OR
               o.size      ILIKE %(q_like)s OR
               (%(id_exact)s IS NOT NULL AND o.id = %(id_exact)s)
        )
        ORDER BY o.created_at DESC NULLS LAST, o.id DESC
        LIMIT %(limit)s
        """
        with _conn() as con, con.cursor() as cur:
            cur.execute(fallback_sql, {"q": q, "q_like": q_like, "id_exact": id_exact, "limit": limit})
            rows = cur.fetchall() or []

    # нормализуем ключи под фронт
    out: List[Dict[str, Any]] = []
    for r in rows:
        # Row may or may not contain joined client/address columns depending on which query ran.
        out.append(
            {
                "id": r.get("id") if isinstance(r, dict) else r[0],
                "order_key": r.get("order_key") if isinstance(r, dict) else r[1],
                "status": r.get("status") if isinstance(r, dict) else r[2],
                "title": r.get("title") if isinstance(r, dict) else r[3],
                "store": r.get("store") if isinstance(r, dict) else r[4],
                "color": r.get("color") if isinstance(r, dict) else r[5],
                "size": r.get("size") if isinstance(r, dict) else r[6],
                "qty": r.get("qty") if isinstance(r, dict) else r[7],
                "price": r.get("price") if isinstance(r, dict) else r[8],
                "currency": r.get("currency") if isinstance(r, dict) else r[9],
                "comment": r.get("comment") if isinstance(r, dict) else r[10],
                "created_at": r.get("created_at") if isinstance(r, dict) else r[11],
                "client": {
                    "id": r.get("client_id") if isinstance(r, dict) else None,
                    "username": r.get("client_username") if isinstance(r, dict) else None,
                    "phone": r.get("client_phone") if isinstance(r, dict) else None,
                },
                "address": {
                    "id": r.get("address_id") if isinstance(r, dict) else None,
                    "city": r.get("address_city") if isinstance(r, dict) else None,
                    "address": r.get("address_line") if isinstance(r, dict) else None,
                    "receiver": r.get("address_receiver") if isinstance(r, dict) else None,
                    "phone": r.get("address_phone") if isinstance(r, dict) else None,
                },
            }
        )
    return out


# ---------- status update ----------

def update_order_status(order_key_or_id: Any, new_status: str, by_login: Optional[str] = None) -> int:
    """
    Обновляет статус заказа. Возвращает кол-во изменённых строк.
    order_key_or_id — либо числовой id, либо текстовый order_key.
    """
    _ensure_schema()
    if new_status is None or (isinstance(new_status, str) and not new_status.strip()):
        return 0

    where = "id = %s"
    params: List[Any] = [order_key_or_id]
    if isinstance(order_key_or_id, str) and not re.fullmatch(r"\d+", order_key_or_id):
        where = "order_key = %s"

    sql = f"""
    UPDATE public.orders
       SET status = %s,
           updated_at = now()
     WHERE {where}
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql, [new_status] + params)
        return cur.rowcount
