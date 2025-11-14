from app.config import SECRET_KEY
from app.utils.security import set_secret_key

# Устанавливаем секретный ключ из конфигурации
set_secret_key(SECRET_KEY)
