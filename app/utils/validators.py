import re
from typing import Optional

ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—_]*([A-Z0-9]{2,})", re.IGNORECASE)
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,})")

def extract_order_id(s: str) -> Optional[str]:
    """Извлечь order_id из текста"""
    if not s:
        return None
    s = s.strip()
    m = ORDER_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2).upper()}"
    
    # fallback: если уже похоже на PREFIX-SUFFIX
    if "-" in s:
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and left.isalpha():
            right_norm = re.sub(r"[^A-Z0-9]+", "", right, flags=re.I)
            if right_norm:
                return f"{left.upper()}-{right_norm.upper()}"
    return None

def extract_usernames(text: str) -> list[str]:
    """Извлечь username из текста"""
    return [m.group(1) for m in USERNAME_RE.finditer(text)]

def is_valid_status(s: str, statuses: list[str]) -> bool:
    """Проверить валидность статуса"""
    return bool(s) and s.strip().lower() in {x.lower() for x in statuses}

def normalize_phone(phone: str) -> Optional[str]:
    """Нормализовать номер телефона"""
    normalized = phone.strip().replace(" ", "").replace("-", "")
    if normalized.startswith("+7"): 
        normalized = "8" + normalized[2:]
    elif normalized.startswith("7"): 
        normalized = "8" + normalized[1:]
    
    if not (normalized.isdigit() and len(normalized) == 11 and normalized.startswith("8")):
        return None
    return normalized

def validate_postcode(postcode: str) -> bool:
    """Валидация почтового индекса"""
    return postcode.isdigit() and 5 <= len(postcode) <= 6
