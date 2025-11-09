# app/sheets.py
# Унифицированный слой работы с "листами":
# - если есть DATABASE_URL/NEON_DB_URL -> используем Postgres (Neon) через repo_pg
# - иначе используем Google Sheets (старый backend)
#
# Совместимость с admin_ui:
#  - sheets.get_worksheet("admins") возвращает объект с методами:
#       get_all_values(), get_all_records(), append_row([...])
#  - sheets._now() доступен в обоих режимах

import os
from datetime import datetime, timezone

# Всегда есть sheets._now()
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_USE_PG = bool(
    os.getenv("DATABASE_URL")
    or os.getenv("NEON_DB_URL")
    or os.getenv("PGHOST")
    or os.getenv("PG_DSN")
)

if _USE_PG:
    # -------------------- Postgres backend (Neon) --------------------
    BACKEND = "pg"

    # Экспортируем все функции репозитория (бот/админка их используют)
    from .repo_pg import *        # noqa: F401,F403
    from . import repo_pg as _pg  # низкоуровневый коннект

    def _ensure_admins_table():
        """Создаёт/поправляет таблицу public.admins до ожидаемой схемы."""
        with _pg._conn() as con, con.cursor() as cur:
            # если таблицы нет — создадим целиком
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
            # а если была — аккуратно добавим недостающие поля/индексы
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
                    # если поле уже есть/индекс есть — просто продолжаем
                    pass

    class _AdminsWS:
        """Эмуляция листа 'admins' (gspread-совместимый интерфейс)."""

        def _rows(self):
            _ensure_admins_table()
            with _pg._conn() as con, con.cursor() as cur:
                # login берём из login, а если его нет — из username (совместимость)
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
            """Список словарей, как у gspread.get_all_records()."""
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
            """
            ВАЖНО: если таблица пуста — вернуть [] (как gspread на пустом листе),
            чтобы в admin_ui сработал "посев" владельца из ENV.
            """
            recs = self.get_all_records()
            if not recs:
                return []
            vals = [["login", "hash", "role", "avatar", "created_at"]]
            for r in recs:
                vals.append([r["login"], r["hash"], r["role"], r["avatar"], r["created_at"]])
            return vals

        def append_row(self, values):
            """
            Принимаем либо заголовок, либо реальную строку:
              ["login","password_hash"/"hash","role","avatar","created_at"]
            Заголовок игнорируем (no-op), реальные данные — upsert по login.
            """
            _ensure_admins_table()

            # 1) Заголовок — ничего не делаем
            if values and isinstance(values[0], str):
                v0 = (values[0] or "").strip().lower()
                v1 = (values[1] or "").strip().lower() if len(values) > 1 else ""
                if v0 == "login" and v1 in ("hash", "password_hash"):
                    return

            # 2) Данные
            login   = values[0] if len(values) > 0 else None
            hash_v  = values[1] if len(values) > 1 else None
            role    = values[2] if len(values) > 2 else None
            avatar  = values[3] if len(values) > 3 else None
            created = values[4] if len(values) > 4 and values[4] else _now()

            if login is None or (isinstance(login, str) and not login.strip()):
                # некорректный логин — пропускаем
                return

            with _pg._conn() as con, con.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.admins (login, hash, role, avatar, created_at)
                    VALUES (%s, %s, COALESCE(%s,'admin'), %s, %s)
                    ON CONFLICT (login) DO UPDATE
                      SET hash   = EXCLUDED.hash,
                          role   = EXCLUDED.role,
                          avatar = EXCLUDED.avatar;
                """, (login, hash_v, role, avatar, created))

    def get_worksheet(name: str):
        if (name or "").strip().lower() == "admins":
            return _AdminsWS()
        # других листов в PG-режиме пока не эмулируем
        raise AttributeError("get_worksheet is only implemented for 'admins' in Postgres mode")

else:
    # -------------------- Google Sheets backend --------------------
    BACKEND = "sheets"
    from .sheets_gs import *              # noqa: F401,F403
    from .sheets_gs import get_worksheet  # оригинальная функция

    # Пробрасываем _now из sheets_gs, если он там есть.
    try:
        from .sheets_gs import _now as _gs_now  # type: ignore
        _now = _gs_now  # переопределим на «родной» для GS
    except Exception:
        # оставим наш универсальный _now()
        pass
