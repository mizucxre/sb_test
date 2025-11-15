#!/usr/bin/env python3
"""Скрипт для инициализации администраторов в базе данных"""

import asyncio
import os
from dotenv import load_dotenv
from app.database import db
from app.utils.security import hash_password

load_dotenv()

async def init_admin_users():
    """Инициализация администраторов"""
    try:
        # Подключаемся к базе данных
        await db.init()
        
        print("🔍 Проверяем существующих администраторов...")
        
        async with db.pool.acquire() as conn:
            # Проверяем, есть ли администраторы
            count = await conn.fetchval("SELECT COUNT(*) FROM admin_users")
            
            if count == 0:
                print("📝 Создаем начального администратора...")
                
                # Создаем супер-администратора
                username = os.getenv("ADMIN_USERNAME", "admin")
                password = os.getenv("ADMIN_PASSWORD", "admin123")
                
                password_hash = hash_password(password)
                
                await conn.execute('''
                    INSERT INTO admin_users (username, email, password_hash, role, avatar_url, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', username, f"{username}@example.com", password_hash, "super_admin", None, True)
                
                print(f"✅ Создан супер-администратор:")
                print(f"   👤 Username: {username}")
                print(f"   🔑 Password: {password}")
                print(f"   👑 Role: super_admin")
                
            else:
                print(f"✅ Найдено {count} администраторов в базе данных")
                
                # Показываем список администраторов
                rows = await conn.fetch("SELECT id, username, role, is_active FROM admin_users ORDER BY id")
                
                print("\n📋 Список администраторов:")
                for row in rows:
                    status = "✅ Активен" if row['is_active'] else "❌ Неактивен"
                    role = "👑 Супер-админ" if row['role'] == "super_admin" else "👤 Админ"
                    print(f"   {row['id']}. {row['username']} - {role} - {status}")
        
        print("\n✅ Инициализация завершена успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации: {e}")

if __name__ == "__main__":
    asyncio.run(init_admin_users())
