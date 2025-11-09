"""
Скрипт для локальной разработки.
Запускает бота в режиме polling вместо webhook.
"""
import asyncio
import logging
from dotenv import load_dotenv
import uvicorn
from app.webhook import app, _build_application
from app.logger import setup_logging

async def main():
    # Загружаем переменные окружения
    load_dotenv()
    
    # Настраиваем логирование
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Запуск в режиме разработки")
    
    # Запускаем API в отдельном потоке
    server = uvicorn.Server(config=uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8080,
        reload=True
    ))
    
    # Создаем и запускаем бота
    application = await _build_application()
    await application.initialize()
    await application.start()
    
    # Запускаем все вместе
    async with asyncio.TaskGroup() as tg:
        tg.create_task(server.serve())
        tg.create_task(application.run_polling())

if __name__ == "__main__":
    asyncio.run(main())