import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import Response
from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from app.database import db
from app.handlers import register_handlers
from app.config import BOT_TOKEN, PUBLIC_URL
from app.web_admin import app as admin_app

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Подключаем веб-админку
app.mount("/admin", admin_app)

application: Application = None

async def _build_application() -> Application:
    """Создаёт Application и регистрирует хэндлеры"""
    logger.info("🔄 Building application...")
    
    app_ = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Регистрация всех хэндлеров
    logger.info("🔄 Registering handlers...")
    register_handlers(app_)
    logger.info("✅ Handlers registered")
    
    # Установка вебхука
    if PUBLIC_URL:
        url = f"{PUBLIC_URL.rstrip('/')}/telegram"
        await app_.bot.set_webhook(url)
        logger.info(f"🌐 Webhook set to: {url}")
    else:
        logger.warning("⚠️ PUBLIC_URL is empty - using polling")
    
    logger.info("✅ Application built successfully")
    return app_
    
@app.on_event("startup")
async def on_startup():
    global application
    try:
        # Подключаем базу данных
        await db.connect()
        logger.info("✅ Database connected successfully")
        
        # Создаем приложение
        application = await _build_application()
        await application.initialize()
        await application.start()
        logger.info("✅ Bot started successfully")
        
        # Проверяем состояние бота
        bot_info = await application.bot.get_me()
        logger.info(f"🤖 Bot @{bot_info.username} is ready!")
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise

@app.on_event("shutdown")
async def on_shutdown():
    if application:
        await application.stop()
        await application.shutdown()
    logger.info("Bot stopped")

@app.post("/telegram")
async def telegram(request: Request):
    """Обработка входящих webhook запросов от Telegram"""
    try:
        data = await request.json()
        logger.info(f"📨 Received webhook update: {data}")
        
        update = Update.de_json(data, application.bot)
        
        # Детальное логирование типа апдейта
        if update.message:
            user = update.message.from_user
            logger.info(f"💬 Message from {user.id} (@{user.username}): '{update.message.text}'")
        elif update.callback_query:
            user = update.callback_query.from_user
            logger.info(f"🔘 Callback from {user.id} (@{user.username}): {update.callback_query.data}")
        elif update.edited_message:
            logger.info(f"✏️ Edited message from {update.edited_message.from_user.id}")
        else:
            logger.info(f"📦 Other update type: {update}")
            
        # Обрабатываем апдейт
        await application.process_update(update)
        logger.info("✅ Update processed successfully")
        
    except Exception as e:
        logger.error(f"❌ Error processing update: {e}")
        logger.error(f"📊 Update data: {data if 'data' in locals() else 'No data'}")
    
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "ok", "message": "SEABLUU Bot is running"}

@app.get("/health")
async def health():
    return {"status": "ok", "database": "connected"}
