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
