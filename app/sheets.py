# app/sheets.py
# Если есть DATABASE_URL — работаем через Postgres, иначе Google Sheets.
# Совместимость с admin_ui: get_worksheet("admins") поддерживает
# get_all_records(), get_all_values(), append_row([...]).
import os

_USE_PG = bool(os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL"))

if _USE_PG:
    BACKEND = "pg"
    from .repo_pg import *        # noqa: F401,F403
    from . import repo_pg as _pg  # доступ к _pg._conn()

    def _ensure_admins_table():
        # Гибкая схема: поддерживаем и "листовую" модель (login/hash/role/avatar/created_at),
        # и то, что могло быть раньше (user_id/username).
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
            # индексы для upsert по login и по user_id (если они используются)
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS admins_login_key ON public.admins(login)")
            except Exception:
                pass
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS admins_user_id_key ON public.admins(user_id)")
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
            recs = self.get_all_records()
            vals = [["login", "hash", "role", "avatar", "created_at"]]
            for r in recs:
                vals.append([r["login"], r["hash"], r["role"], r["avatar"], r["created_at"]])
            return vals

        def append_row(self, values):
            # ожидается [login, hash, role, avatar, created_at]
            _ensure_admins_table()
            login   = values[0] if len(values) > 0 else None
            hash_v  = values[1] if len(values) > 1 else None
            role    = values[2] if len(values) > 2 else None
            avatar  = values[3] if len(values) > 3 else None
            created = values[4] if len(values) > 4 else None
            with _pg._conn() as con, con.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.admins (login, hash, role, avatar, created_at)
                    VALUES (%s, %s, COALESCE(%s,'admin'), %s, COALESCE(%s, now()))
                    ON CONFLICT (login) DO UPDATE
                      SET hash   = EXCLUDED.hash,
                          role   = EXCLUDED.role,
                          avatar = EXCLUDED.avatar;
                """, (login, hash_v, role, avatar, created))

    def get_worksheet(name: str):
        if (name or "").strip().lower() == "admins":
            return _AdminsWS()
        raise AttributeError("get_worksheet is only implemented for 'admins' in Postgres mode")

else:
    BACKEND = "sheets"
    from .sheets_gs import *              # noqa: F401,F403
    from .sheets_gs import get_worksheet  # оригинальная функция
