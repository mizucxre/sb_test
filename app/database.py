import asyncpg
import os
from typing import Optional

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Подключение к Neon PostgreSQL"""
        database_url = os.getenv("NEON_DATABASE_URL")
        if not database_url:
            raise ValueError("NEON_DATABASE_URL not set in environment variables")
        
        self.pool = await asyncpg.create_pool(database_url)
        await self.init_tables()
    
    async def init_tables(self):
        """Инициализация таблиц"""
        async with self.pool.acquire() as conn:
            # Таблица заказов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    order_id VARCHAR(50) UNIQUE NOT NULL,
                    client_name TEXT,
                    phone VARCHAR(20),
                    origin VARCHAR(100),
                    status VARCHAR(100) NOT NULL,
                    note TEXT,
                    country VARCHAR(10),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            
            # Таблица участников
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS participants (
                    id SERIAL PRIMARY KEY,
                    order_id VARCHAR(50) NOT NULL,
                    username VARCHAR(100) NOT NULL,
                    paid BOOLEAN DEFAULT FALSE,
                    qty INTEGER,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(order_id, username)
                )
            ''')
            
            # Таблица адресов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS addresses (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(100),
                    full_name TEXT NOT NULL,
                    phone VARCHAR(20) NOT NULL,
                    city VARCHAR(100) NOT NULL,
                    address TEXT NOT NULL,
                    postcode VARCHAR(20) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            
            # Таблица подписок
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id BIGINT,
                    order_id VARCHAR(50),
                    last_sent_status VARCHAR(100),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, order_id)
                )
            ''')
            
            # Индексы для производительности
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_participants_order_id ON participants(order_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_participants_username ON participants(username)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)')

# Глобальный экземпляр базы данных
db = Database()
