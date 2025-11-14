from telegram.ext import Application
import logging

logger = logging.getLogger(__name__)

def register_handlers(application: Application):
    """Регистрация всех хэндлеров в правильном порядке"""
    from . import callback_handlers, client_handlers, admin_handlers, excel_handlers
    
    # Правильный порядок: специфичные -> общие
    callback_handlers.register(application)
    excel_handlers.register(application)
    admin_handlers.register(application)  # Админы перед клиентами
    client_handlers.register(application)  # Клиенты последние
    
    logger.info("✅ Все хэндлеры успешно зарегистрированы!")
