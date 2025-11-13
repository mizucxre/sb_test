from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class Order:
    order_id: str
    client_name: str = ""
    phone: str = ""
    origin: str = ""
    status: str = ""
    note: str = ""
    country: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class Participant:
    order_id: str
    username: str
    paid: bool = False
    qty: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class Address:
    user_id: int
    username: str
    full_name: str
    phone: str
    city: str
    address: str
    postcode: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class Subscription:
    user_id: int
    order_id: str
    last_sent_status: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
