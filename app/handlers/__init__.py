from telegram.ext import Application

def register_handlers(application: Application):
    """Регистрация всех хэндлеров"""
    from . import client_handlers, admin_handlers, callback_handlers
    
    client_handlers.register(application)
    admin_handlers.register(application) 
    callback_handlers.register(application)
