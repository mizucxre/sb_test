# app/webhook.py
import os
import logging
from typing import List, Dict, Any

from fastapi import FastAPI, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from .main import register_handlers
try:
    from .main import register_admin_ui
except Exception:
    register_admin_ui = None  # безопасно, если функции нет

# БД/хранилище: storage сам выберет Postgres (repo_pg.py), если есть DATABASE_URL
from . import storage as sheets

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Подключаем UI-роутер админки как и было
from .admin_ui import get_admin_router
app.include_router(get_admin_router(), prefix="/admin")

application: Application | None = None


def _get_bot_token() -> str:
    # 1) из config.py
    try:
        from .config import BOT_TOKEN as _TOK  # type: ignore
        if _TOK:
            return _TOK
    except Exception:
        pass
    # 2) из окружения
    env_tok = os.getenv("BOT_TOKEN", "")
    if not env_tok:
        raise RuntimeError("BOT_TOKEN is not set (neither in app.config nor in environment)")
    return env_tok


def _get_public_url() -> str:
    # 1) из config.py
    try:
        from .config import PUBLIC_URL as _URL  # type: ignore
        if _URL:
            return _URL
    except Exception:
        pass
    # 2) из окружения
    return os.getenv("PUBLIC_URL", "")


async def _build_application() -> Application:
    """Создаёт Application, регистрирует хэндлеры, настраивает вебхук (без инициализации)."""
    bot_token = _get_bot_token()
    public_url = _get_public_url()

    app_ = ApplicationBuilder().token(bot_token).build()

    # базовые хэндлеры
    register_handlers(app_)

    # админ-UI (если есть)
    if register_admin_ui:
        try:
            register_admin_ui(app_)
            logger.info("Admin UI handlers registered.")
        except Exception as e:
            logger.warning("Admin UI not registered: %s", e)

    # вебхук
    if public_url:
        url = f"{public_url.rstrip('/')}/telegram"
        await app_.bot.set_webhook(url)
        logger.info("Webhook set to %s", url)
    else:
        logger.warning("PUBLIC_URL is empty or missing; skipping setWebhook")

    return app_


async def _ensure_ready():
    """Гарантирует, что Application создано, initialize()/start() вызваны."""
    global application
    if application is None:
        application = await _build_application()

    # initialize + start обязательны в PTB v21 при внешнем фреймворке
    try:
        await application.initialize()
    except Exception:
        # если уже инициализировано — ок
        pass
    try:
        await application.start()
    except Exception:
        # если уже запущено — ок
        pass


@app.on_event("startup")
async def on_startup():
    global application
    application = await _build_application()
    # ВАЖНО: инициализация и старт
    await application.initialize()
    await application.start()
    logger.info("Startup complete: application initialized & started.")


@app.on_event("shutdown")
async def on_shutdown():
    # Корректное завершение
    if application is not None:
        try:
            await application.stop()
        finally:
            await application.shutdown()
    logger.info("Shutdown complete.")


@app.post("/telegram")
async def telegram(request: Request):
    await _ensure_ready()

    data = await request.json()
    try:
        update = Update.de_json(data, application.bot)
        # диагностика
        try:
            utype = (
                "message" if getattr(update, "message", None) else
                "callback_query" if getattr(update, "callback_query", None) else
                "other"
            )
            logger.info("[webhook] incoming update: type=%s", utype)
        except Exception:
            pass

        await application.process_update(update)
    except Exception as e:
        logger.exception("Error processing update: %s", e)

    return Response(status_code=200)


# ------------------- ADMIN API -------------------

def _dedup_by_order_id(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for r in rows or []:
        oid = r.get("order_id")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        out.append(r)
    return out


@app.get("/admin/api/search")
def admin_search(q: str = Query("", alias="q"), limit: int = 200):
    """
    Поиск заказов для веб-админки.
    - пустой q -> последние заказы
    - q == order_id (точное совпадение)
    - q начинается с @ -> по username
    - q содержит цифры -> по телефону
    """
    q = (q or "").strip()
    rows: List[Dict[str, Any]] = []

    if not q:
        rows = sheets.list_recent_orders(limit=limit)
    else:
        # 1) точное совпадение по order_id
        o = sheets.get_order(q)
        if o:
            rows.append(o)
        # 2) по @username
        if q.startswith("@"):
            rows += sheets.get_orders_by_username(q)
        # 3) по телефону (цифры)
        if any(ch.isdigit() for ch in q):
            rows += sheets.get_orders_by_phone(q)

    rows = _dedup_by_order_id(rows)

    # обогащаем участниками
    items = []
    for r in rows:
        oid = r.get("order_id")
        participants = sheets.get_participants(oid) if oid else []
        items.append({
            "order_id": oid,
            "status": r.get("status"),
            "client_name": r.get("client_name"),
            "phone": r.get("phone"),
            "participants": participants,
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        })

    return {"items": items, "count": len(items)}


class BulkStatusReq(BaseModel):
    ids: List[str]
    status: str


@app.post("/admin/api/bulk-status")
def admin_bulk_status(req: BulkStatusReq):
    """
    Массовое обновление статуса заказов из админки.
    """
    updated = 0
    for oid in (req.ids or []):
        if oid:
            sheets.update_order_status(oid, req.status)
            updated += 1
    return {"ok": True, "updated": updated}


# -------------------------------------------------


@app.get("/health")
async def health():
    return {"ok": True}
