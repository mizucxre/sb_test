import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext

# Конфигурация для хэширования паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Секретный ключ для JWT
SECRET_KEY = "your-secret-key-change-in-production"  # Замените в продакшене
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней

def hash_password(password: str) -> str:
    """Хэширование пароля"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля"""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Создание JWT токена"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    """Проверка JWT токена"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def generate_avatar_url(username: str, email: Optional[str] = None, size: int = 64) -> str:
    """Генерация URL аватарки через Gravatar или по умолчанию"""
    if email:
        # Используем Gravatar если есть email
        email_hash = hashlib.md5(email.lower().encode()).hexdigest()
        return f"https://www.gravatar.com/avatar/{email_hash}?s={size}&d=identicon"
    else:
        # Генерируем аватар с инициалами
        initials = username[:2].upper() if len(username) >= 2 else username.upper()
        return f"https://ui-avatars.com/api/?name={initials}&background=random&size={size}"
