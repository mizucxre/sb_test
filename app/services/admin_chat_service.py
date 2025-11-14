import logging
from typing import List
from app.database import db
from app.models import AdminChatMessage

logger = logging.getLogger(__name__)

class AdminChatService:
    
    @staticmethod
    async def get_recent_messages(limit: int = 50) -> List[AdminChatMessage]:
        """Получить последние сообщения чата"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT 
                        acm.id,
                        acm.user_id,
                        au.username,
                        acm.message,
                        acm.is_system,
                        acm.created_at,
                        au.avatar_url
                    FROM admin_chat_messages acm
                    JOIN admin_users au ON acm.user_id = au.id
                    ORDER BY acm.created_at DESC
                    LIMIT $1
                ''', limit)
                
                messages = []
                for row in rows:
                    message_dict = dict(row)
                    messages.append(AdminChatMessage(**message_dict))
                
                # Возвращаем в правильном порядке (от старых к новым)
                return list(reversed(messages))
                
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return []
    
    @staticmethod
    async def create_message(user_id: int, message: str) -> AdminChatMessage:
        """Создать новое сообщение в чате"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    INSERT INTO admin_chat_messages (user_id, message)
                    VALUES ($1, $2)
                    RETURNING id, user_id, message, is_system, created_at
                ''', user_id, message)
                
                # Получаем информацию о пользователе для ответа
                user_row = await conn.fetchrow(
                    "SELECT username, avatar_url FROM admin_users WHERE id = $1",
                    user_id
                )
                
                message_dict = dict(row)
                message_dict['username'] = user_row['username']
                message_dict['avatar_url'] = user_row['avatar_url']
                
                return AdminChatMessage(**message_dict)
                
        except Exception as e:
            logger.error(f"Error creating chat message: {e}")
            raise
    
    @staticmethod
    async def create_system_message(message: str) -> AdminChatMessage:
        """Создать системное сообщение"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    INSERT INTO admin_chat_messages (user_id, message, is_system)
                    VALUES (0, $1, TRUE)
                    RETURNING id, user_id, message, is_system, created_at
                ''', message)
                
                message_dict = dict(row)
                message_dict['username'] = 'System'
                message_dict['avatar_url'] = None
                
                return AdminChatMessage(**message_dict)
                
        except Exception as e:
            logger.error(f"Error creating system message: {e}")
            raise
