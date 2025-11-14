import logging
from .client_handlers import register as register_client_handlers  
from .callback_handlers import register as register_callback_handlers
from .text_handler import register as register_text_handler

logger = logging.getLogger(__name__)

def register_handlers(application):
    """Регистрация всех хэндлеров"""
    
    # 1. Команды
    register_client_handlers(application)
    
    # 2. Callback хэндлеры (inline кнопки)
    register_callback_handlers(application)
    
    # 3. Текстовые сообщения
    register_text_handler(application)
    
    logger.info("✅ Все хэндлеры зарегистрированы")
