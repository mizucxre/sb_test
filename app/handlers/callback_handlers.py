import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler

from app.config import ADMIN_IDS, STATUSES
from app.utils.helpers import reply_animated, reply_markdown_animated, _is_admin
from app.services.order_service import OrderService, ParticipantService
from app.services.user_service import SubscriptionService, AddressService
from app.utils.keyboards import status_keyboard, build_participants_kb
from app.utils.helpers import build_participants_text

logger = logging.getLogger(__name__)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback запросов от inline кнопок"""
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data.startswith("addr:"):
            await _handle_address_callbacks(update, context, data)
        elif data.startswith("adm:"):
            await _handle_admin_callbacks(update, context, data)
        elif data.startswith("mass:"):
            await _handle_mass_callbacks(update, context, data)
        elif data.startswith(("sub:", "unsub:")):
            await _handle_subscription_callbacks(update, context, data)
        elif data.startswith("pp:"):
            await _handle_participant_callbacks(update, context, data)
    except Exception as e:
        logger.error(f"Error handling callback {data}: {e}")
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

async def _handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка админских callback"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return

    if data.startswith("adm:status_menu:"):
        # Меню смены статуса
        order_id = data.split(":", 2)[2]
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        rows = [[InlineKeyboardButton(s, callback_data=f"adm:set_status_val:{order_id}:{i}")] 
                for i, s in enumerate(STATUSES)]
        await reply_animated(update, context, "Выберите новый статус:", 
                            reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("adm:set_status_val:"):
        # Установка статуса
        _, _, order_id, idx_s = data.split(":")
        try:
            idx = int(idx_s)
            new_status = STATUSES[idx]
        except (ValueError, IndexError):
            await reply_animated(update, context, "❌ Некорректный выбор статуса")
            return

        success = await OrderService.update_order_status(order_id, new_status)
        if success:
            await reply_markdown_animated(update, context, f"✨ Статус *{order_id}* обновлён на: _{new_status}_ ✅")
            # Уведомляем подписчиков
            try:
                await _notify_subscribers(context.application, order_id, new_status)
            except Exception as e:
                logger.error(f"Failed to notify subscribers: {e}")
        else:
            await reply_animated(update, context, "❌ Заказ не найден")

    elif data.startswith("adm:pick_status_id:"):
        # Выбор статуса при добавлении заказа
        _, _, idx_s = data.split(":")
        try:
            idx = int(idx_s)
            chosen = STATUSES[idx]
        except (ValueError, IndexError):
            await reply_animated(update, context, "❌ Некорректный выбор статуса")
            return

        context.user_data.setdefault("adm_buf", {})["status"] = chosen
        context.user_data["adm_mode"] = "add_order_note"
        await reply_animated(update, context, "Примечание (или '-' если нет):")

async def _handle_mass_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка массовых операций"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return

    if data.startswith("mass:pick_status_id:"):
        _, _, idx_s = data.split(":")
        try:
            idx = int(idx_s)
            new_status = STATUSES[idx]
        except (ValueError, IndexError):
            await reply_animated(update, context, "❌ Некорректный выбор статуса")
            return

        context.user_data["adm_mode"] = "mass_update_status_ids"
        context.user_data["mass_status"] = new_status
        await reply_markdown_animated(
            update, context,
            f"✅ Новый статус: *{new_status}*\n\nТеперь пришли список `order_id`:\n"
            "• через пробел, запятые или с новой строки\n"
            "• пример: `CN-1001 CN-1002, KR-2003`"
        )

async def _handle_subscription_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка подписок"""
    user_id = update.effective_user.id
    
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        success = await SubscriptionService.subscribe(user_id, order_id)
        if success:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            await update.callback_query.edit_message_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
            )
            await reply_animated(update, context, "✅ Подписка оформлена! Буду присылать обновления 🔔")
    
    elif data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        success = await SubscriptionService.unsubscribe(user_id, order_id)
        if success:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            await update.callback_query.edit_message_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться", callback_data=f"sub:{order_id}")]])
            )
            await reply_animated(update, context, "✅ Отписка выполнена")

async def _handle_participant_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка операций с участниками"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return

    if data.startswith("pp:toggle:"):
        # Переключение статуса оплаты
        _, _, order_id, username = data.split(":", 3)
        success = await ParticipantService.toggle_participant_paid(order_id, username)
        
        if success:
            participants = await ParticipantService.get_participants(order_id)
            page = 0
            txt = build_participants_text(order_id, participants, page, 8)
            kb = build_participants_kb(order_id, participants, page, 8)
            
            try:
                await update.callback_query.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
            except Exception:
                await reply_markdown_animated(update, context, txt, reply_markup=kb)

    elif data.startswith("pp:refresh:"):
        # Обновление списка участников
        parts = data.split(":")
        order_id = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        
        participants = await ParticipantService.get_participants(order_id)
        await update.callback_query.message.edit_text(
            build_participants_text(order_id, participants, page, 8),
            reply_markup=build_participants_kb(order_id, participants, page, 8),
            parse_mode="Markdown"
        )

    elif data.startswith("pp:page:"):
        # Пагинация участников
        _, _, order_id, page_s = data.split(":")
        page = int(page_s)
        
        participants = await ParticipantService.get_participants(order_id)
        await update.callback_query.message.edit_text(
            build_participants_text(order_id, participants, page, 8),
            reply_markup=build_participants_kb(order_id, participants, page, 8),
            parse_mode="Markdown"
        )

async def _notify_subscribers(application, order_id: str, new_status: str):
    """Уведомление подписчиков (вспомогательная функция)"""
    subs = await SubscriptionService.get_all_subscriptions()
    targets = [s for s in subs if s.order_id == order_id]
    
    for sub in targets:
        try:
            await application.bot.send_message(
                chat_id=sub.user_id,
                text=f"🔄 Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                parse_mode="Markdown",
            )
            await SubscriptionService.set_last_sent_status(sub.user_id, order_id, new_status)
        except Exception as e:
            logger.warning(f"Failed to notify subscriber {sub.user_id}: {e}")

def register(application):
    """Регистрация callback хэндлеров"""
    application.add_handler(CallbackQueryHandler(handle_callback))
