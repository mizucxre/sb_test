import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from app.config import ADMIN_IDS
from app.utils.helpers import reply_animated, reply_markdown_animated, _is_admin
from app.utils.keyboards import MAIN_KB
from app.services.user_service import AddressService, SubscriptionService
from app.services.order_service import OrderService, ParticipantService
from app.utils.validators import extract_order_id, extract_usernames, normalize_phone, validate_postcode
from app.models import Address

logger = logging.getLogger(__name__)

# Текст кнопок для идентификации - ДОБАВЬТЕ ЭМОДЗИ
CLIENT_ALIASES = {
    "track": {"🔍 отследить разбор", "отследить разбор"},
    "addrs": {"🏠 мои адреса", "мои адреса"}, 
    "subs": {"🔔 мои подписки", "мои подписки"},
    "cancel": {"❌ отмена", "отмена", "cancel"},
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    hello = (
        "✨ Привет! Я *SEABLUU* Helper — помогу отследить разборы, адреса и подписки.\n\n"
        "*Что умею:*\n"
        "• 🔍 Отследить разбор — статус по `order_id` (например, `CN-12345`).\n"
        "• 🔔 Подписки — уведомлю, когда статус заказа изменится.\n"
        "• 🏠 Мои адреса — сохраню/обновлю адрес для доставки.\n\n"
        "Если что-то пошло не так — нажми «Отмена» или используй /help."
    )
    await reply_markdown_animated(update, context, hello, reply_markup=MAIN_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await reply_animated(
        update, context,
        "📘 Помощь:\n"
        "• 🔍 Отследить разбор — статус по номеру\n"
        "• 🏠 Мои адреса — добавить/изменить адрес\n"
        "• 🔔 Мои подписки — список подписок\n"
        "• /admin — админ-панель (для админов)"
    )

def _is_text(text: str, group: set[str]) -> bool:
    """Проверка соответствия текста группе алиасов"""
    text_lower = text.strip().lower()
    group_lower = {x.lower() for x in group}
    return text_lower in group_lower

async def handle_client_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ВСЕХ текстовых сообщений от пользователей"""
    user_id = update.effective_user.id
    raw_text = (update.message.text or "").strip()
    text = raw_text
    
    logger.info(f"📨 Получено сообщение от {user_id}: {raw_text}")

    # Пропускаем админов - их сообщения обрабатываются в admin_handlers
    if _is_admin(user_id, ADMIN_IDS):
        logger.info(f"Сообщение от админа {user_id}, пропускаем")
        return

    # Обработка кнопок главного меню
    if _is_text(text, CLIENT_ALIASES["cancel"]):
        context.user_data.clear()
        await reply_animated(update, context, "Отменили действие. Что дальше? 🙂", reply_markup=MAIN_KB)
        return

    if _is_text(text, CLIENT_ALIASES["track"]):
        context.user_data["mode"] = "track"
        await reply_animated(update, context, "🔎 Отправьте номер заказа (например: CN-12345):")
        return

    if _is_text(text, CLIENT_ALIASES["addrs"]):
        context.user_data["mode"] = None
        await show_addresses(update, context)
        return

    if _is_text(text, CLIENT_ALIASES["subs"]):
        context.user_data["mode"] = None
        await show_subscriptions(update, context)
        return

    # Обработка режимов
    mode = context.user_data.get("mode")
    if mode == "track":
        await query_status(update, context, raw_text)
        return

    # Мастер добавления адреса
    if mode == "add_address_fullname":
        context.user_data["full_name"] = raw_text
        await reply_animated(update, context, "📞 Телефон (пример: 87001234567):")
        context.user_data["mode"] = "add_address_phone"
        return

    if mode == "add_address_phone":
        normalized = normalize_phone(raw_text)
        if not normalized:
            await reply_animated(update, context, "Нужно 11 цифр и обязательно с 8. Пример: 87001234567\nВведи номер ещё раз или нажми «Отмена».")
            return
        context.user_data["phone"] = normalized
        await reply_animated(update, context, "🏙 Город (пример: Астана):")
        context.user_data["mode"] = "add_address_city"
        return

    if mode == "add_address_city":
        context.user_data["city"] = raw_text
        await reply_animated(update, context, "🏠 Адрес (свободный формат):")
        context.user_data["mode"] = "add_address_address"
        return

    if mode == "add_address_address":
        context.user_data["address"] = raw_text
        await reply_animated(update, context, "📮 Почтовый индекс (пример: 010000):")
        context.user_data["mode"] = "add_address_postcode"
        return

    if mode == "add_address_postcode":
        if not validate_postcode(raw_text):
            await reply_animated(update, context, "Индекс выглядит странно. Пример: 010000\nВведи индекс ещё раз или нажми «Отмена».")
            return
        context.user_data["postcode"] = raw_text
        await save_address(update, context)
        return

    # Если ничего не подошло - показываем главное меню
    logger.info(f"❓ Не распознано сообщение от {user_id}: {raw_text}")
    await reply_animated(
        update, context,
        "Хмм, не понял. Выберите кнопку ниже или введите номер заказа. Если что — «Отмена».",
        reply_markup=MAIN_KB,
    )

# ... остальные функции (query_status, show_addresses, save_address, show_subscriptions) остаются без изменений

def register(application):
    """Регистрация клиентских хэндлеров"""
    # Сначала команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    
    # Затем ВСЕ текстовые сообщения - этот хэндлер должен быть последним
    application.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND), 
        handle_client_text
    ))
    
    logger.info("✅ Клиентские хэндлеры зарегистрированы")
