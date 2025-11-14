import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from app.config import ADMIN_IDS
from app.utils.helpers import _is_admin
from . import client_handlers, admin_handlers

logger = logging.getLogger(__name__)

async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик всех текстовых сообщений"""
    user_id = update.effective_user.id
    raw_text = (update.message.text or "").strip()
    
    logger.info(f"📨 Текстовое сообщение от {user_id}: {raw_text}")
    
    # Перенаправляем админов в admin_handlers
    if _is_admin(user_id, ADMIN_IDS):
        logger.info(f"Перенаправляем сообщение админа {user_id} в admin_handlers")
        # Здесь должна быть логика обработки админских сообщений
        # Пока просто возвращаем админ-меню
        from app.utils.keyboards import ADMIN_MENU_KB
        from app.utils.helpers import reply_animated
        await reply_animated(update, context, "Админ-панель", reply_markup=ADMIN_MENU_KB)
        return
    
    # Все остальные пользователи обрабатываются в client_handlers
    await client_handlers.handle_client_text(update, context)

def register(application):
    """Регистрация единого текстового обработчика"""
    application.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND),
        handle_all_text
    ))
    logger.info("✅ Единый текстовый хэндлер зарегистрирован")
