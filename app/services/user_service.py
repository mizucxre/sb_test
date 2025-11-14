import logging
from typing import List, Optional
from app.models import Address, Subscription
from app.database import db

logger = logging.getLogger(__name__)

class AddressService:
    
    @staticmethod
    async def upsert_address(address: Address) -> bool:
        """Добавить или обновить адрес"""
        try:
            async with db.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO addresses (user_id, username, full_name, phone, city, address, postcode)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    phone = EXCLUDED.phone,
                    city = EXCLUDED.city,
                    address = EXCLUDED.address,
                    postcode = EXCLUDED.postcode,
                    updated_at = NOW()
                ''', address.user_id, address.username, address.full_name, 
                   address.phone, address.city, address.address, address.postcode)
                return True
        except Exception as e:
            logger.error(f"Error upserting address for user {address.user_id}: {e}")
            return False
    
    @staticmethod
    async def list_addresses(user_id: int) -> List[Address]:
        """Получить адреса пользователя"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM addresses WHERE user_id = $1",
                    user_id
                )
                return [Address(**dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Error listing addresses for user {user_id}: {e}")
            return []
    
    @staticmethod
    async def delete_address(user_id: int) -> bool:
        """Удалить адрес пользователя"""
        try:
            async with db.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM addresses WHERE user_id = $1",
                    user_id
                )
                return "DELETE 1" in result
        except Exception as e:
            logger.error(f"Error deleting address for user {user_id}: {e}")
            return False
    
    @staticmethod
    async def get_addresses_by_usernames(usernames: List[str]) -> List[Address]:
        """Получить адреса по списку username"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM addresses WHERE username = ANY($1)",
                    [u.lower().lstrip('@') for u in usernames]
                )
                return [Address(**dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Error getting addresses by usernames: {e}")
            return []
    
    @staticmethod
    async def get_user_ids_by_usernames(usernames: List[str]) -> List[int]:
        """Получить user_id по username"""
        try:
            addresses = await AddressService.get_addresses_by_usernames(usernames)
            return [addr.user_id for addr in addresses]
        except Exception as e:
            logger.error(f"Error getting user IDs by usernames: {e}")
            return []

class SubscriptionService:
    
    @staticmethod
    async def is_subscribed(user_id: int, order_id: str) -> bool:
        """Проверить подписку пользователя"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM subscriptions WHERE user_id = $1 AND order_id = $2",
                    user_id, order_id
                )
                return row is not None
        except Exception as e:
            logger.error(f"Error checking subscription: {e}")
            return False
    
    @staticmethod
    async def subscribe(user_id: int, order_id: str) -> bool:
        """Подписать пользователя на заказ"""
        try:
            async with db.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO subscriptions (user_id, order_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, order_id) DO UPDATE SET
                    updated_at = NOW()
                ''', user_id, order_id)
                return True
        except Exception as e:
            logger.error(f"Error subscribing user {user_id} to {order_id}: {e}")
            return False
    
    @staticmethod
    async def unsubscribe(user_id: int, order_id: str) -> bool:
        """Отписать пользователя от заказа"""
        try:
            async with db.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM subscriptions WHERE user_id = $1 AND order_id = $2",
                    user_id, order_id
                )
                return "DELETE 1" in result
        except Exception as e:
            logger.error(f"Error unsubscribing user {user_id} from {order_id}: {e}")
            return False
    
    @staticmethod
    async def list_subscriptions(user_id: int) -> List[Subscription]:
        """Получить подписки пользователя"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM subscriptions WHERE user_id = $1",
                    user_id
                )
                return [Subscription(**dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Error listing subscriptions for user {user_id}: {e}")
            return []
    
    @staticmethod
    async def get_all_subscriptions() -> List[Subscription]:
        """Получить все подписки (для рассылки)"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM subscriptions")
                return [Subscription(**dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all subscriptions: {e}")
            return []
    
    @staticmethod
    async def set_last_sent_status(user_id: int, order_id: str, status: str) -> bool:
        """Обновить последний отправленный статус"""
        try:
            async with db.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO subscriptions (user_id, order_id, last_sent_status)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, order_id) DO UPDATE SET
                    last_sent_status = EXCLUDED.last_sent_status,
                    updated_at = NOW()
                ''', user_id, order_id, status)
                return True
        except Exception as e:
            logger.error(f"Error setting last sent status: {e}")
            return False
