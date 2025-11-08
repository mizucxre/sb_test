# app/sheets.py
# Унифицированный слой: если есть DATABASE_URL — работаем через Postgres,
# иначе используем Google Sheets. Плюс совместимость для admin_ui: get_worksheet("admins").

import os

_USE_PG = bool(os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL"))

if _USE_PG:
    # ---- Postgres backend ----
    BACKEND = "pg"

    # Экспорт всех функций репозитория (get_order, list_recent_orders, ...)
    from .repo_pg import *        # noqa: F401,F403
    from . import repo_pg as _pg  # чтобы обратиться к _pg._conn() внутри заглушки ниже

    class _AdminsWS:
        """Совместимость с admin_ui: имитируем часть API Google Sheets для листа 'admins'."""
        def get_all_records(self):
            with _pg._conn() as con, con.cursor() as cur:   # row_factory=dict_row уже задан в repo_pg
                cur.execute("""
                    select user_id, username, role, created_at
                    from admins
                    order by created_at asc
                """)
                rows = cur.fetchall() or []
                # Приводим к формату get_all_records(): список словарей
                out = []
                for r in rows:
                    out.append({
                        "user_id": r.get("user_id"),
                        "username": (r.get("username") or "") if r else "",
                        "role": (r.get("role") or "") if r else "",
                    })
                return out

        # При необходимости можно будет добавить append_row()/update, если админка начнёт их вызывать.
        # Сейчас admin_ui читает роли и флаги владельца — get_all_records достаточно.

    def get_worksheet(name: str):
        """Поддерживаем только 'admins' в режиме Postgres."""
        if (name or "").strip().lower() == "admins":
            return _AdminsWS()
        raise AttributeError("get_worksheet is only implemented for 'admins' in Postgres mode")

else:
    # ---- Google Sheets backend (старое поведение) ----
    BACKEND = "sheets"
    from .sheets_gs import *              # noqa: F401,F403
    from .sheets_gs import get_worksheet  # просто пробрасываем оригинальную функцию
