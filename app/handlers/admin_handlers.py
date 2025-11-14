import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from app.config import ADMIN_IDS
from app.utils.helpers import reply_animated, reply_markdown_animated, _is_admin
from app.utils.keyboards import ADMIN_MENU_KB

logger = logging.getLogger(__name__)

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin - вход в админ-панель"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    
    # Очищаем состояние
    for key in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id", "mass_status"):
        context.user_data.pop(key, None)
    
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)

def register(application):
    """Регистрация админских хэндлеров"""
    # Только команда /admin - текстовые сообщения обрабатываются в основном обработчике
    application.add_handler(CommandHandler("admin", admin_menu))
    logger.info("✅ Админские хэндлеры зарегистрированы")
