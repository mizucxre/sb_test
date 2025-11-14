from .callback_handlers import register as register_callbacks
from .client_handlers import register as register_clients

def register_handlers(application):
    """Регистрация всех хэндлеров"""
    register_callbacks(application)
    register_clients(application)
