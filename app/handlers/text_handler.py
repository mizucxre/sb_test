import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from app.config import ADMIN_IDS
from app.utils.helpers import _is_admin
from app.handlers.admin_handlers import handle_admin_text
from app.handlers.client_handlers import handle_client_text

logger = logging.getLogger(__name__)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    
    try:
        # Если пользователь админ - передаем в админский обработчик
        if _is_admin(user_id, ADMIN_IDS):
            await handle_admin_text(update, context)
        else:
            # Иначе в клиентский обработчик
            await handle_client_text(update, context)
    except Exception as e:
        logger.error(f"Ошибка в текстовом обработчике: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке сообщения")

def register(application):
    """Регистрация текстового хэндлера"""
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    logger.info("✅ Текстовый хэндлер зарегистрирован")
