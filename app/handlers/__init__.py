from telegram.ext import Application
import logging

logger = logging.getLogger(__name__)

def register_handlers(application: Application):
    """Регистрация всех хэндлеров"""
    from . import client_handlers, admin_handlers, callback_handlers
    
    # Регистрируем в правильном порядке
    callback_handlers.register(application)
    client_handlers.register(application)
    admin_handlers.register(application)
    
    logger.info("✅ Все хэндлеры успешно зарегистрированы!")
