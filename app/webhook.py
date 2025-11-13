import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import Response
from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from app.database import db
from app.handlers import register_handlers
from app.config import BOT_TOKEN, PUBLIC_URL

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()
application: Application = None

async def _build_application() -> Application:
    """Создаёт Application и регистрирует хэндлеры"""
    app_ = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Регистрация всех хэндлеров
    register_handlers(app_)
    
    # Установка вебхука
    if PUBLIC_URL:
        url = f"{PUBLIC_URL.rstrip('/')}/telegram"
        await app_.bot.set_webhook(url)
        logger.info("Webhook set to %s", url)
    else:
        logger.warning("PUBLIC_URL is empty - using polling")
    
    return app_

@app.on_event("startup")
async def on_startup():
    global application
    # Подключаем базу данных
    await db.connect()
    logger.info("Database connected")
    
    # Создаем приложение
    application = await _build_application()
    await application.initialize()
    await application.start()
    logger.info("Bot started successfully")

@app.on_event("shutdown")
async def on_shutdown():
    if application:
        await application.stop()
        await application.shutdown()
    logger.info("Bot stopped")

@app.post("/telegram")
async def telegram(request: Request):
    data = await request.json()
    try:
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error("Error processing update: %s", e)
    
    return Response(status_code=200)

@app.get("/health")
async def health():
    return {"status": "ok", "database": "connected"}
