from telegram.ext import Application

def register_handlers(application: Application):
    """Регистрация всех хэндлеров"""
    from . import client_handlers, admin_handlers, callback_handlers
    
    # Регистрируем в правильном порядке - сначала колбэки, потом сообщения
    callback_handlers.register(application)
    client_handlers.register(application)
    admin_handlers.register(application)
