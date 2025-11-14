import logging
from typing import List, Dict
from app.database import db
from app.services.user_service import AddressService

logger = logging.getLogger(__name__)

class BroadcastService:
    
    @staticmethod
    async def send_telegram_message(user_id: int, message: str) -> bool:
        """Отправка сообщения через Telegram бота"""
        try:
            # Импортируем здесь чтобы избежать циклических импортов
            # Замените на ваш способ получения экземпляра бота
            from app.main import bot
            
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML'
            )
            return True
        except Exception as e:
            logger.error(f"Error sending Telegram message to {user_id}: {e}")
            return False
    
    @staticmethod
    async def broadcast_to_unpaid_users(message: str) -> Dict:
        """Рассылка сообщения неплательщикам"""
        try:
            # Получаем всех неплательщиков
            from app.services.order_service import ParticipantService
            unpaid_grouped = await ParticipantService.get_all_unpaid_grouped()
            
            if not unpaid_grouped:
                return {"sent": 0, "failed": 0, "total": 0}
            
            # Собираем все username
            all_usernames = []
            for usernames in unpaid_grouped.values():
                all_usernames.extend(usernames)
            
            # Получаем user_id по username
            user_ids = await AddressService.get_user_ids_by_usernames(all_usernames)
            
            sent_count = 0
            failed_count = 0
            
            # Отправляем сообщения
            for user_id in user_ids:
                success = await BroadcastService.send_telegram_message(user_id, message)
                if success:
                    sent_count += 1
                else:
                    failed_count += 1
            
            return {
                "sent": sent_count,
                "failed": failed_count,
                "total": len(user_ids)
            }
            
        except Exception as e:
            logger.error(f"Error in broadcast_to_unpaid_users: {e}")
            return {"sent": 0, "failed": 0, "total": 0}
