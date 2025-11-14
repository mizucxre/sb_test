import logging
from typing import List, Optional
from app.database import db
from app.models import AdminUser, AdminUserCreate, AdminUserUpdate
from app.utils.security import hash_password, verify_password, generate_avatar_url

logger = logging.getLogger(__name__)

class AdminService:
    
    @staticmethod
    async def authenticate_user(username: str, password: str) -> Optional[AdminUser]:
        """Аутентификация пользователя"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, email, password_hash, role, avatar_url, is_active, last_login, created_at, updated_at FROM admin_users WHERE username = $1 AND is_active = TRUE",
                    username
                )
                
                if not row:
                    return None
                
                if not verify_password(password, row['password_hash']):
                    return None
                
                # Преобразуем row в AdminUser
                user_dict = dict(row)
                return AdminUser(**user_dict)
                
        except Exception as e:
            logger.error(f"Error authenticating user {username}: {e}")
            return None
    
    @staticmethod
    async def get_user_by_id(user_id: int) -> Optional[AdminUser]:
        """Получить пользователя по ID"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, email, role, avatar_url, is_active, last_login, created_at, updated_at FROM admin_users WHERE id = $1",
                    user_id
                )
                
                if not row:
                    return None
                
                user_dict = dict(row)
                return AdminUser(**user_dict)
                
        except Exception as e:
            logger.error(f"Error getting user by id {user_id}: {e}")
            return None
    
    @staticmethod
    async def get_user_by_username(username: str) -> Optional[AdminUser]:
        """Получить пользователя по username"""
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, email, role, avatar_url, is_active, last_login, created_at, updated_at FROM admin_users WHERE username = $1",
                    username
                )
                
                if not row:
                    return None
                
                user_dict = dict(row)
                return AdminUser(**user_dict)
                
        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            return None
    
    @staticmethod
    async def get_all_users() -> List[AdminUser]:
        """Получить всех пользователей"""
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, username, email, role, avatar_url, is_active, last_login, created_at, updated_at FROM admin_users ORDER BY created_at DESC"
                )
                
                users = []
                for row in rows:
                    user_dict = dict(row)
                    users.append(AdminUser(**user_dict))
                
                return users
                
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
    
    @staticmethod
    async def create_user(user_data: AdminUserCreate) -> AdminUser:
        """Создать нового пользователя"""
        try:
            async with db.pool.acquire() as conn:
                # Генерируем аватарку
                avatar_url = generate_avatar_url(user_data.username, user_data.email)
                
                # Хэшируем пароль
                password_hash = hash_password(user_data.password)
                
                row = await conn.fetchrow('''
                    INSERT INTO admin_users (username, email, password_hash, role, avatar_url)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, username, email, role, avatar_url, is_active, last_login, created_at, updated_at
                ''', user_data.username, user_data.email, password_hash, user_data.role, avatar_url)
                
                user_dict = dict(row)
                return AdminUser(**user_dict)
                
        except Exception as e:
            logger.error(f"Error creating user {user_data.username}: {e}")
            raise
    
    @staticmethod
    async def update_user(user_id: int, user_data: AdminUserUpdate) -> Optional[AdminUser]:
        """Обновить пользователя"""
        try:
            async with db.pool.acquire() as conn:
                # Собираем поля для обновления
                update_fields = []
                values = []
                i = 1
                
                if user_data.email is not None:
                    update_fields.append(f"email = ${i}")
                    values.append(user_data.email)
                    i += 1
                
                if user_data.role is not None:
                    update_fields.append(f"role = ${i}")
                    values.append(user_data.role)
                    i += 1
                
                if user_data.avatar_url is not None:
                    update_fields.append(f"avatar_url = ${i}")
                    values.append(user_data.avatar_url)
                    i += 1
                
                if user_data.is_active is not None:
                    update_fields.append(f"is_active = ${i}")
                    values.append(user_data.is_active)
                    i += 1
                
                if user_data.password is not None:
                    update_fields.append(f"password_hash = ${i}")
                    values.append(hash_password(user_data.password))
                    i += 1
                
                if not update_fields:
                    return await AdminService.get_user_by_id(user_id)
                
                values.append(user_id)
                query = f"""
                    UPDATE admin_users 
                    SET {', '.join(update_fields)}, updated_at = NOW()
                    WHERE id = ${i}
                    RETURNING id, username, email, role, avatar_url, is_active, last_login, created_at, updated_at
                """
                
                row = await conn.fetchrow(query, *values)
                if not row:
                    return None
                
                user_dict = dict(row)
                return AdminUser(**user_dict)
                
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return None
    
    @staticmethod
    async def delete_user(user_id: int) -> bool:
        """Удалить пользователя"""
        try:
            async with db.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM admin_users WHERE id = $1",
                    user_id
                )
                return "DELETE 1" in result
                
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            return False
    
    @staticmethod
    async def update_last_login(user_id: int) -> bool:
        """Обновить время последнего входа"""
        try:
            async with db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE admin_users SET last_login = NOW() WHERE id = $1",
                    user_id
                )
                return True
                
        except Exception as e:
            logger.error(f"Error updating last login for user {user_id}: {e}")
            return False
    
    @staticmethod
    async def change_password(user_id: int, current_password: str, new_password: str) -> bool:
        """Смена пароля"""
        try:
            async with db.pool.acquire() as conn:
                # Получаем текущий хэш пароля
                current_hash = await conn.fetchval(
                    "SELECT password_hash FROM admin_users WHERE id = $1",
                    user_id
                )
                
                if not current_hash or not verify_password(current_password, current_hash):
                    return False
                
                # Обновляем пароль
                new_hash = hash_password(new_password)
                await conn.execute(
                    "UPDATE admin_users SET password_hash = $1, updated_at = NOW() WHERE id = $2",
                    new_hash, user_id
                )
                
                return True
                
        except Exception as e:
            logger.error(f"Error changing password for user {user_id}: {e}")
            return False
