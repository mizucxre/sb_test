# app/sheets.py
# Унифицированный слой: если есть DATABASE_URL — работаем через Postgres,
# иначе Google Sheets. Совместимость с admin_ui: get_worksheet("admins")
import os

_USE_PG = bool(os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL"))

if _USE_PG:
    BACKEND = "pg"
    from .repo_pg import *        # noqa: F401,F403
    from . import repo_pg as _pg  # для низкоуровневого коннекта в заглушке

    class _AdminsWS:
        """Эмуляция листа 'admins' для кода admin_ui (gspread-совместимый интерфейс)."""

        def _rows(self):
            with _pg._conn() as con, con.cursor() as cur:
                cur.execute("""
                    select user_id, username, role
                    from admins
                    order by created_at asc
                """)
                return cur.fetchall() or []

        def get_all_records(self):
            """Список словарей — как у gspread.get_all_records()."""
            out = []
            for r in self._rows():
                out.append({
                    "user_id": r.get("user_id"),
                    "username": r.get("username") or "",
                    "role": r.get("role") or "",
                })
            return out

        def get_all_values(self):
            """2D-массив (включая заголовок) — как у gspread.get_all_values()."""
            records = self.get_all_records()
            values = [["user_id", "username", "role"]]
            for r in records:
                values.append([r.get("user_id"), r.get("username"), r.get("role")])
            return values

    def get_worksheet(name: str):
        if (name or "").strip().lower() == "admins":
            return _AdminsWS()
        # если вдруг попросят другой лист — честно скажем, что не реализовано
        raise AttributeError("get_worksheet is only implemented for 'admins' in Postgres mode")

else:
    BACKEND = "sheets"
    from .sheets_gs import *              # noqa: F401,F403
    from .sheets_gs import get_worksheet  # пробрасываем оригинальную функцию
