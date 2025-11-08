# storage.py — small facade that swaps Google Sheets to Postgres by env var
import os
USE_PG = bool(os.getenv("DATABASE_URL") or os.getenv("NEON_DB_URL") or os.getenv("SUPABASE_DB_URL"))
if USE_PG:
    from .repo_pg import *   # noqa
else:
    from .sheets import *    # noqa