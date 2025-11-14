import logging
from .admin_handlers import register as register_admin_handlers
from .client_handlers import register as register_client_handlers  
from .callback_handlers import register as register_callback_handlers
from .text_handler import register as register_text_handler
from .excel_handlers import register as register_excel_handlers

logger = logging.getLogger(__name__)

def register_handlers(application):
    """Регистрация всех хэндлеров в правильном порядке"""
    
    # 1. Сначала команды
    register_client_handlers(application)  # /start, /help
    
    # 2. Админские команды  
    register_admin_handlers(application)   # /admin
    
    # 3. Callback хэндлеры (inline кнопки)
    register_callback_handlers(application)
    
    # 4. Текстовые сообщения (кнопки клавиатуры) - ЕДИНСТВЕННЫЙ обработчик текста
    register_text_handler(application)
    
    # 5. Документы (Excel)
    register_excel_handlers(application)
    
    logger.info("✅ Все хэндлеры зарегистрированы")
