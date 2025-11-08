# app/sheets.py
# Унифицированный слой: если есть DATABASE_URL — работаем через Postgres,
# иначе продолжаем использовать Google Sheets.
import os

if os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL"):
    from .repo_pg import *     # функции add_order, get_order, ... идут в Neon
    BACKEND = "pg"
else:
    from .sheets_gs import *   # старое поведение: Google Sheets
    BACKEND = "sheets"
