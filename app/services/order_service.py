import logging
from typing import List, Optional, Dict, Any
from app.models import Order, Participant
from app.database import db

logger = logging.getLogger(__name__)

class OrderService:
    
    @staticmethod
    async def get_order(order_id: str) -> Optional[Order]:
        """Получить заказ по ID"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT order_id, client_name, phone, origin, status, note, country, created_at, updated_at FROM orders WHERE order_id = $1", 
                    order_id
                )
                if row:
                    # Преобразуем row в dict и убираем поле 'id' если оно есть
                    order_dict = dict(row)
                    if 'id' in order_dict:
                        del order_dict['id']
                    return Order(**order_dict)
            return None
        except Exception as e:
            logger.error(f"Error getting order {order_id}: {e}")
            return None
    
    @staticmethod
    async def add_order(order: Order) -> bool:
        """Добавить новый заказ"""
        try:
            async with db.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO orders (order_id, client_name, phone, origin, status, note, country)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (order_id) DO UPDATE SET
                    client_name = EXCLUDED.client_name,
                    phone = EXCLUDED.phone,
                    origin = EXCLUDED.origin,
                    status = EXCLUDED.status,
                    note = EXCLUDED.note,
                    country = EXCLUDED.country,
                    updated_at = NOW()
                ''', order.order_id, order.client_name, order.phone, order.origin, 
                   order.status, order.note, order.country)
                return True
        except Exception as e:
            logger.error(f"Error adding order {order.order_id}: {e}")
            return False
    
    @staticmethod
    async def update_order_status(order_id: str, new_status: str) -> bool:
        """Обновить статус заказа"""
        try:
            async with db.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE orders SET status = $1, updated_at = NOW() WHERE order_id = $2",
                    new_status, order_id
                )
                return "UPDATE 1" in result
        except Exception as e:
            logger.error(f"Error updating order status {order_id}: {e}")
            return False
    
    @staticmethod
    async def get_orders_by_note(marker: str) -> List[Order]:
        """Найти заказы по метке в примечании"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT order_id, client_name, phone, origin, status, note, country, created_at, updated_at FROM orders WHERE note ILIKE $1",
                    f"%{marker}%"
                )
                orders = []
                for row in rows:
                    order_dict = dict(row)
                    if 'id' in order_dict:
                        del order_dict['id']
                    orders.append(Order(**order_dict))
                return orders
        except Exception as e:
            logger.error(f"Error getting orders by note: {e}")
            return []
    
    @staticmethod
    async def list_recent_orders(limit: int = 20) -> List[Order]:
        """Список последних заказов"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT order_id, client_name, phone, origin, status, note, country, created_at, updated_at FROM orders ORDER BY updated_at DESC LIMIT $1",
                    limit
                )
                orders = []
                for row in rows:
                    order_dict = dict(row)
                    if 'id' in order_dict:
                        del order_dict['id']
                    orders.append(Order(**order_dict))
                return orders
        except Exception as e:
            logger.error(f"Error listing recent orders: {e}")
            return []
    
    @staticmethod
    async def list_orders_by_status(statuses: List[str]) -> List[Order]:
        """Список заказов по статусам"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT order_id, client_name, phone, origin, status, note, country, created_at, updated_at FROM orders WHERE status = ANY($1) ORDER BY updated_at DESC",
                    statuses
                )
                orders = []
                for row in rows:
                    order_dict = dict(row)
                    if 'id' in order_dict:
                        del order_dict['id']
                    orders.append(Order(**order_dict))
                return orders
        except Exception as e:
            logger.error(f"Error listing orders by status: {e}")
            return []

    @staticmethod
    async def update_order(order_id: str, update_data: dict) -> bool:
        """Обновление данных заказа"""
        try:
            async with db.pool.acquire() as conn:
                set_parts = []
                values = []
                i = 1
                
                for key, value in update_data.items():
                    if key in ["client_name", "country", "note", "status"]:
                        set_parts.append(f"{key} = ${i}")
                        values.append(value)
                        i += 1
                
                if not set_parts:
                    return False
                
                values.append(order_id)
                query = f"UPDATE orders SET {', '.join(set_parts)}, updated_at = NOW() WHERE order_id = ${i}"
                
                result = await conn.execute(query, *values)
                return "UPDATE 1" in result
                
        except Exception as e:
            logger.error(f"Error updating order {order_id}: {e}")
            return False

    @staticmethod
    async def delete_order(order_id: str) -> bool:
        """Удаление заказа и связанных данных"""
        try:
            async with db.pool.acquire() as conn:
                async with conn.transaction():
                    # Удаляем участников
                    await conn.execute(
                        "DELETE FROM participants WHERE order_id = $1",
                        order_id
                    )
                    
                    # Удаляем подписки
                    await conn.execute(
                        "DELETE FROM subscriptions WHERE order_id = $1", 
                        order_id
                    )
                    
                    # Удаляем заказ
                    result = await conn.execute(
                        "DELETE FROM orders WHERE order_id = $1",
                        order_id
                    )
                    
                    return "DELETE 1" in result
                    
        except Exception as e:
            logger.error(f"Error deleting order {order_id}: {e}")
            return False

class ParticipantService:
    
    @staticmethod
    async def ensure_participants(order_id: str, usernames: List[str]) -> bool:
        """Добавить участников, если их ещё нет"""
        try:
            async with db.pool.acquire() as conn:
                for username in usernames:
                    await conn.execute('''
                        INSERT INTO participants (order_id, username, paid)
                        VALUES ($1, $2, FALSE)
                        ON CONFLICT (order_id, username) DO NOTHING
                    ''', order_id, username.lower().lstrip('@'))
                return True
        except Exception as e:
            logger.error(f"Error ensuring participants for {order_id}: {e}")
            return False
    
    @staticmethod
    async def get_participants(order_id: str) -> List[Participant]:
        """Получить участников заказа"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT order_id, username, paid, created_at, updated_at FROM participants WHERE order_id = $1 ORDER BY username",
                    order_id
                )
                participants = []
                for row in rows:
                    participant_dict = dict(row)
                    if 'id' in participant_dict:
                        del participant_dict['id']
                    participants.append(Participant(**participant_dict))
                return participants
        except Exception as e:
            logger.error(f"Error getting participants for {order_id}: {e}")
            return []
    
    @staticmethod
    async def toggle_participant_paid(order_id: str, username: str) -> bool:
        """Переключить статус оплаты участника"""
        try:
            async with db.pool.acquire() as conn:
                # Сначала получаем текущее значение
                current = await conn.fetchval(
                    "SELECT paid FROM participants WHERE order_id = $1 AND username = $2",
                    order_id, username.lower().lstrip('@')
                )
                
                if current is None:
                    return False
                
                new_paid = not current
                result = await conn.execute(
                    "UPDATE participants SET paid = $1, updated_at = NOW() WHERE order_id = $2 AND username = $3",
                    new_paid, order_id, username.lower().lstrip('@')
                )
                return "UPDATE 1" in result
        except Exception as e:
            logger.error(f"Error toggling participant paid status: {e}")
            return False
    
    @staticmethod
    async def get_unpaid_usernames(order_id: str) -> List[str]:
        """Получить список username неплательщиков"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT username FROM participants WHERE order_id = $1 AND paid = FALSE",
                    order_id
                )
                return [row['username'] for row in rows]
        except Exception as e:
            logger.error(f"Error getting unpaid usernames: {e}")
            return []
    
    @staticmethod
    async def get_all_unpaid_grouped() -> Dict[str, List[str]]:
        """Сгруппировать всех неплательщиков по order_id"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT order_id, username FROM participants WHERE paid = FALSE ORDER BY order_id"
                )
                
                grouped = {}
                for row in rows:
                    order_id = row['order_id']
                    username = row['username']
                    if order_id not in grouped:
                        grouped[order_id] = []
                    grouped[order_id].append(username)
                
                return grouped
        except Exception as e:
            logger.error(f"Error getting all unpaid grouped: {e}")
            return {}
    
    @staticmethod
    async def find_orders_for_username(username: str) -> List[str]:
        """Найти заказы по username участника"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT order_id FROM participants WHERE username = $1",
                    username.lower().lstrip('@')
                )
                return [row['order_id'] for row in rows]
        except Exception as e:
            logger.error(f"Error finding orders for username: {e}")
            return []
