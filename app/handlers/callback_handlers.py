import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler

from app.utils.helpers import reply_animated
from app.services.user_service import SubscriptionService, AddressService

logger = logging.getLogger(__name__)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback запросов от inline кнопок"""
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data.startswith("addr:"):
            await _handle_address_callbacks(update, context, data)
        elif data.startswith(("sub:", "unsub:")):
            await _handle_subscription_callbacks(update, context, data)
        else:
            logger.warning(f"Необработанный callback: {data}")
            await reply_animated(update, context, "❌ Неизвестный запрос")
    except Exception as e:
        logger.error(f"Ошибка обработки callback {data}: {e}")
        await reply_animated(update, context, "❌ Произошла ошибка при обработке запроса")

async def _handle_address_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка callback для адресов"""
    if data == "addr:add":
        context.user_data["mode"] = "add_address_fullname"
        await reply_animated(update, context, "Давайте добавим/обновим адрес.\n👤 ФИО:")
    elif data == "addr:del":
        user_id = update.effective_user.id
        success = await AddressService.delete_address(user_id)
        if success:
            await reply_animated(update, context, "✅ Адрес удалён")
        else:
            await reply_animated(update, context, "❌ Адрес не найден")

async def _handle_subscription_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка подписок"""
    user_id = update.effective_user.id
    
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        success = await SubscriptionService.subscribe(user_id, order_id)
        if success:
            await update.callback_query.edit_message_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
            )
            await reply_animated(update, context, "✅ Подписка оформлена! Буду присылать обновления 🔔")
    
    elif data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        success = await SubscriptionService.unsubscribe(user_id, order_id)
        if success:
            await update.callback_query.edit_message_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться", callback_data=f"sub:{order_id}")]])
            )
            await reply_animated(update, context, "✅ Отписка выполнена")

def register(application):
    """Регистрация callback хэндлеров"""
    application.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("✅ Callback хэндлеры зарегистрированы")
