# -*- coding: utf-8 -*-
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row


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
        cur.execute("CREATE INDEX IF NOT EXISTS orders_status_idx  ON public.orders(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS orders_client_idx  ON public.orders(client_id);")
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
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql, {"q": q, "q_like": q_like, "id_exact": id_exact, "limit": limit})
        rows = cur.fetchall() or []

    # нормализуем ключи под фронт
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "order_key": r["order_key"],
                "status": r["status"],
                "title": r["title"],
                "store": r["store"],
                "color": r["color"],
                "size": r["size"],
                "qty": r["qty"],
                "price": r["price"],
                "currency": r["currency"],
                "comment": r["comment"],
                "created_at": r["created_at"],
                "client": {
                    "id": r["client_id"],
                    "username": r["client_username"],
                    "phone": r["client_phone"],
                },
                "address": {
                    "id": r["address_id"],
                    "city": r["address_city"],
                    "address": r["address_line"],
                    "receiver": r["address_receiver"],
                    "phone": r["address_phone"],
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
