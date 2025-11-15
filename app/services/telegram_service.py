import logging
import httpx
from typing import List, Optional, Dict
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

class TelegramChannelService:
    """Сервис для получения постов из Telegram канала"""
    
    def __init__(self):
        self.channel_url = "https://t.me/seabluushop"
        self.cache = []
        self.last_update = None
        
    async def get_channel_posts(self, limit: int = 5) -> List[Dict]:
        """
        Получение последних постов из канала.
        В реальной реализации здесь будет интеграция с Telegram API.
        Сейчас возвращаем заглушки с красивым оформлением.
        """
        # Проверяем кэш (кэшируем на 30 минут)
        if self.cache and self.last_update and (datetime.now() - self.last_update).total_seconds() < 1800:
            return self.cache[:limit]
        
        try:
            # В реальной реализации здесь будет запрос к Telegram API
            # Сейчас создаем красивые заглушки
            posts = [
                {
                    "id": 1,
                    "title": "🔥 Новые поступления!",
                    "content": "В нашем магазине появились новые товары от ведущих брендов. Успейте заказать первыми!",
                    "image_url": "/static/images/channel-post-1.jpg",
                    "date": "2024-01-15T14:30:00",
                    "views": 1250,
                    "likes": 89
                },
                {
                    "id": 2,
                    "title": "🎉 Скидка 20% на все заказы",
                    "content": "Только до конца недели действует специальная скидка для наших подписчиков!",
                    "image_url": "/static/images/channel-post-2.jpg",
                    "date": "2024-01-14T10:15:00",
                    "views": 980,
                    "likes": 67
                },
                {
                    "id": 3,
                    "title": "📦 Обновление статусов заказов",
                    "content": "Все заказы за последнюю неделю уже обработаны и отправлены клиентам.",
                    "image_url": "/static/images/channel-post-3.jpg",
                    "date": "2024-01-13T16:45:00",
                    "views": 743,
                    "likes": 42
                },
                {
                    "id": 4,
                    "title": "🌟 Отзывы клиентов",
                    "content": "Благодарим всех за доверие и положительные отзывы о нашей работе!",
                    "image_url": "/static/images/channel-post-4.jpg",
                    "date": "2024-01-12T09:20:00",
                    "views": 1120,
                    "likes": 78
                },
                {
                    "id": 5,
                    "title": "🛒 Как сделать заказ",
                    "content": "Подробная инструкция по оформлению заказа для новых клиентов.",
                    "image_url": "/static/images/channel-post-5.jpg",
                    "date": "2024-01-11T11:30:00",
                    "views": 890,
                    "likes": 55
                }
            ]
            
            self.cache = posts
            self.last_update = datetime.now()
            
            return posts[:limit]
            
        except Exception as e:
            logger.error(f"Error fetching Telegram channel posts: {e}")
            # Возвращаем пустой список в случае ошибки
            return []
    
    def format_post_date(self, date_str: str) -> str:
        """Форматирование даты поста"""
        try:
            date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            now = datetime.now()
            diff = now - date
            
            if diff.days > 7:
                return date.strftime('%d.%m.%Y')
            elif diff.days > 0:
                return f"{diff.days} дн. назад"
            elif diff.seconds > 3600:
                hours = diff.seconds // 3600
                return f"{hours} ч. назад"
            elif diff.seconds > 60:
                minutes = diff.seconds // 60
                return f"{minutes} мин. назад"
            else:
                return "только что"
        except:
            return date_str

# Глобальный экземпляр сервиса
telegram_service = TelegramChannelService()
