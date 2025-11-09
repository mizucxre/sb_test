# -*- coding: utf-8 -*-
"""
sheets.py — совместимый слой для admin_ui:
 - Если есть DATABASE_URL/NEON_DB_URL → используем Postgres (Neon) через psycopg (sync).
 - Иначе — старый Google Sheets backend (sheets_gs).

Важно: для входа админки ожидаются поля: login, password_hash, role, avatar, created_at.
"""
import os
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pg_dsn() -> str:
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("NEON_DB_URL")
        or os.getenv("SUPABASE_DB_URL")
        or ""
    )


_USE_PG = bool(_pg_dsn())

if _USE_PG:
    BACKEND = "pg"
    import psycopg
    from psycopg.rows import dict_row

    def _conn():
        dsn = _pg_dsn()
        if not dsn:
            raise RuntimeError("DATABASE_URL/NEON_DB_URL is not set")
        return psycopg.connect(dsn, row_factory=dict_row)

    def _ensure_admins_table():
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.admins (
                  user_id       bigint,
                  username      text,
                  login         text UNIQUE,
                  password_hash text,
                  role          text DEFAULT 'admin',
                  avatar        text,
                  created_at    timestamptz DEFAULT now(),
                  updated_at    timestamptz
                );
                """
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS admins_login_key ON public.admins(login);"
            )

    class _AdminsWS:
        """gspread-like wrapper for 'admins' sheet backed by Postgres."""
        HEADER = ["login", "password_hash", "role", "avatar", "created_at"]

        def _rows(self):
            _ensure_admins_table()
            with _conn() as con, con.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(login, username) AS login,
                           password_hash,
                           role,
                           avatar,
                           created_at
                    FROM public.admins
                    ORDER BY created_at NULLS LAST, login ASC
                    """
                )
                return cur.fetchall() or []

        def get_all_records(self):
            return [
                {
                    "login": r.get("login") or "",
                    "password_hash": r.get("password_hash") or "",
                    "role": r.get("role") or "",
                    "avatar": r.get("avatar") or "",
                    "created_at": r.get("created_at"),
                }
                for r in self._rows()
            ]

        def get_all_values(self):
            recs = self.get_all_records()
            if not recs:
                return []
            vals = [self.HEADER[:]]
            for r in recs:
                vals.append(
                    [
                        r["login"],
                        r["password_hash"],
                        r["role"],
                        r["avatar"],
                        r["created_at"],
                    ]
                )
            return vals

        def append_row(self, values):
            # если прислали заголовок — игнорируем, чтобы не писать "created_at" как текст в timestamptz
            if values and isinstance(values[0], str):
                v0 = (values[0] or "").strip().lower()
                v1 = (values[1] or "").strip().lower() if len(values) > 1 else ""
                if v0 == "login" and v1 in ("password_hash", "hash"):
                    return

            login = values[0] if len(values) > 0 else None
            pwhash = values[1] if len(values) > 1 else None
            role = values[2] if len(values) > 2 else None
            avatar = values[3] if len(values) > 3 else None
            created = values[4] if len(values) > 4 and values[4] else _now()

            if not login or (isinstance(login, str) and not login.strip()):
                return

            _ensure_admins_table()
            with _conn() as con, con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.admins (login, password_hash, role, avatar, created_at)
                    VALUES (%s,%s,COALESCE(%s,'admin'),%s,%s)
                    ON CONFLICT (login) DO UPDATE
                    SET password_hash=EXCLUDED.password_hash,
                        role=EXCLUDED.role,
                        avatar=EXCLUDED.avatar,
                        updated_at=now()
                    """,
                    (login, pwhash, role, avatar, created),
                )

        # best-effort: чтобы не падать, если где-то вызывается точечное обновление
        def update_cell(self, row_index: int, col_index: int, value):
            try:
                idx = row_index - 2
                if idx < 0:
                    return
                recs = self.get_all_records()
                if idx >= len(recs):
                    return
                login = recs[idx]["login"]
                if col_index == 2:
                    sql = (
                        "update public.admins "
                        "set password_hash=%s, updated_at=now() where login=%s"
                    )
                elif col_index == 3:
                    sql = (
                        "update public.admins "
                        "set role=%s, updated_at=now() where login=%s"
                    )
                elif col_index == 4:
                    sql = (
                        "update public.admins "
                        "set avatar=%s, updated_at=now() where login=%s"
                    )
                else:
                    return
                with _conn() as con, con.cursor() as cur:
                    cur.execute(sql, (value, login))
            except Exception:
                pass

        def delete_rows(self, row_index: int):
            try:
                idx = row_index - 2
                if idx < 0:
                    return
                recs = self.get_all_records()
                if idx >= len(recs):
                    return
                login = recs[idx]["login"]
                with _conn() as con, con.cursor() as cur:
                    cur.execute("delete from public.admins where login=%s", (login,))
            except Exception:
                pass

    class _OrdersWS:
        """Упрощённая заглушка для совместимости с admin_ui."""
        def update_status(self, order_key, status, by_login=None):
            from . import repo_pg as _pg
            return _pg.update_order_status(order_key, status, by_login)

    def get_worksheet(name: str):
        lname = (name or "").strip().lower()
        if lname == "admins":
            return _AdminsWS()
        if lname == "orders":
            return _OrdersWS()
        raise AttributeError(
            "get_worksheet is only implemented for 'admins' and 'orders' in Postgres mode"
        )

else:
    BACKEND = "sheets"
    from .sheets_gs import *  # noqa
    from .sheets_gs import get_worksheet  # noqa
    try:
        from .sheets_gs import _now as _gs_now
        _now = _gs_now
    except Exception:
        pass
