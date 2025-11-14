from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class Order(BaseModel):
    order_id: str
    client_name: str
    phone: Optional[str] = None
    origin: Optional[str] = None
    status: str
    note: Optional[str] = None
    country: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class Participant(BaseModel):
    order_id: str
    username: str
    paid: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class Address(BaseModel):
    user_id: int
    username: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    postcode: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class Subscription(BaseModel):
    user_id: int
    order_id: str
    last_sent_status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Новые модели для системы аккаунтов
class AdminUser(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    role: str = "admin"
    avatar_url: Optional[str] = None
    is_active: bool = True
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AdminUserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    password: str
    role: str = "admin"

class AdminUserUpdate(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: Optional[bool] = None

class AdminChatMessage(BaseModel):
    id: int
    user_id: int
    username: str
    message: str
    is_system: bool = False
    created_at: Optional[datetime] = None
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True

class AdminChatMessageCreate(BaseModel):
    message: str
