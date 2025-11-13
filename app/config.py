import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# Database Configuration
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL")

# Webhook Configuration (если нужно)
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
PORT = int(os.getenv("PORT", "8080"))

# Status Configuration
STATUSES = [
    "🛒 выкуплен",
    "📦 отправка на адрес (Корея)",
    "📦 отправка на адрес (Китай)",
    "📬 приехал на адрес (Корея)",
    "📬 приехал на адрес (Китай)",
    "🛫 ожидает доставку в Казахстан",
    "🚚 отправлен на адрес в Казахстан",
    "🏠 приехал админу в Казахстан",
    "📦 ожидает отправку по Казахстану",
    "🚚 отправлен по Казахстану",
    "✅ получен заказчиком",
]

UNPAID_STATUS = "доставка не оплачена"
