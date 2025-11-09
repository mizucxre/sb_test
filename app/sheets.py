# app/sheets.py
# Если есть DATABASE_URL — работаем через Postgres, иначе Google Sheets.
# Совместимость с admin_ui: get_worksheet("admins") поддерживает
# get_all_records(), get_all_values(), append_row([...]); также есть _now().
import os

_USE_PG = bool(os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL"))

if _USE_PG:
    BACKEND = "pg"
    from datetime import datetime, timezone

    from .repo_pg import *        # noqa: F401,F403
    from . import repo_pg as _pg  # доступ к _pg._conn()

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _ensure_admins_table():
        # Создадим таблицу, если её нет, и добавим недостающие поля/индексы
        with _pg._conn() as con, con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.admins (
                  user_id    bigint,
                  username   text,
                  login      text,
                  hash       text,
                  role       text DEFAULT 'admin',
                  avatar     text,
                  created_at timestamptz DEFAULT now()
                );
            """)
            for stmt in [
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS user_id bigint",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS username text",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS login text",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS hash text",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS role text DEFAULT 'admin'",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS avatar text",
                "ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now()",
                "CREATE UNIQUE INDEX IF NOT EXISTS admins_login_key   ON public.admins(login)",
                "CREATE UNIQUE INDEX IF NOT EXISTS admins_user_id_key ON public.admins(user_id)"
            ]:
                try:
                    cur.execute(stmt)
                except Exception:
                    pass

    class _AdminsWS:
        """Эмуляция листа 'admins' (gspread-совместимый интерфейс)."""

        def _rows(self):
            _ensure_admins_table()
            with _pg._conn() as con, con.cursor() as cur:
                cur.execute("""
                    SELECT
                      COALESCE(login, username) AS login,
                      hash,
                      role,
                      avatar,
                      created_at
                    FROM public.admins
                    ORDER BY created_at NULLS LAST, login ASC
                """)
                return cur.fetchall() or []

        def get_all_records(self):
            out = []
            for r in self._rows():
                out.append({
                    "login": r.get("login") or "",
                    "hash": r.get("hash") or "",
                    "role": r.get("role") or "",
                    "avatar": r.get("avatar") or "",
                    "created_at": r.get("created_at"),
                })
            return out

        def get_all_values(self):
            """Возвращаем [] если реально нет ни одной записи (как gspread на пустом листе)."""
            recs = self.get_all_records()
            if not recs:
                return []
            vals = [["login", "hash", "role", "avatar", "created_at"]]
            for r in recs:
                vals.append([r["login"], r["hash"], r["role"], r["avatar"], r["created_at"]])
            return vals

        def append_row(self, values):
            # ожидается [login, hash|password_hash, role, avatar, created_at]
            _ensure_admins_table()

            # --- 1) Заголовок листа: просто игнорируем ---
            if values and isinstance(values[0], str):
                v0 = (values[0] or "").strip().lower()
                v1 = (values[1] or "").strip().lower() if len(values) > 1 else ""
                if v0 == "login" and v1 in ("hash", "password_hash"):
                    return  # header row -> no-op

            # --- 2) Обычная строка данных ---
            login   = values[0] if len(values) > 0 else None
            hash_v  = values[1] if len(values) > 1 else None
            role    = values[2] if len(values) > 2 else None
            avatar  = values[3] if len(values) > 3 else None
            created = values[4] if len(values) > 4 and values[4] else _now()

            with _pg._conn() as con, con.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.admins (login, hash, role, avatar, created_at)
                    VALUES (%s, %s, COALESCE(%s,'admin'), %s, %s)
                    ON CONFLICT (login) DO UPDATE
                      SET hash   = EXCLUDED.hash,
                          role   = EXCLUDED.role,
                          avatar = EXCLUDED.avatar;
                """, (login, hash_v, role, avatar, created))

