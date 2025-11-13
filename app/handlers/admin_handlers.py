import logging
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from app.config import ADMIN_IDS, STATUSES
from app.utils.helpers import reply_animated, reply_markdown_animated, _is_admin
from app.utils.keyboards import ADMIN_MENU_KB, status_keyboard, order_card_kb, build_participants_kb
from app.services.order_service import OrderService, ParticipantService
from app.services.user_service import AddressService, SubscriptionService
from app.models import Order
from app.utils.validators import extract_order_id, extract_usernames, is_valid_status
from app.utils.helpers import build_participants_text

logger = logging.getLogger(__name__)

# Админские алиасы
ADMIN_MENU_ALIASES = {
    "admin_add": {"➕ добавить разбор", "добавить разбор"},
    "admin_track": {"🔎 отследить разбор", "отследить разбор"},
    "admin_send": {"📣 админ: рассылка", "админ: рассылка"},
    "admin_addrs": {"📇 админ: адреса", "админ: адреса"},
    "admin_reports": {"📊 отчёты", "отчёты"},
    "admin_mass": {"🧰 массовая смена статусов", "массовая смена статусов"},
    "admin_exit": {"🚪 выйти из админ-панели", "выйти из админ-панели"},
}

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin - вход в админ-панель"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    
    # Очищаем состояние
    for key in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id", "mass_status"):
        context.user_data.pop(key, None)
    
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)

def _is_text(text: str, group: set[str]) -> bool:
    """Проверка соответствия текста группе алиасов"""
    return text.strip().lower() in {x.lower() for x in group}

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений админа"""
    user_id = update.effective_user.id
    if not _is_admin(user_id, ADMIN_IDS):
        return

    raw_text = (update.message.text or "").strip()
    text = raw_text.lower()

    # Выход из админки
    if _is_text(text, ADMIN_MENU_ALIASES["admin_exit"]):
        context.user_data.clear()
        from app.utils.keyboards import MAIN_KB
        await reply_animated(update, context, "🚪 Готово, вышли из админ-панели.", reply_markup=MAIN_KB)
        return

    # Основное меню админки
    if _is_text(text, ADMIN_MENU_ALIASES["admin_add"]):
        context.user_data["adm_mode"] = "add_order_id"
        context.user_data["adm_buf"] = {}
        await reply_markdown_animated(update, context, "➕ Введи *order_id* (например: `CN-12345`):")
        return

    if _is_text(text, ADMIN_MENU_ALIASES["admin_track"]):
        context.user_data["adm_mode"] = "find_order"
        await reply_markdown_animated(update, context, "🔎 Введи *order_id* для поиска:")
        return

    if _is_text(text, ADMIN_MENU_ALIASES["admin_mass"]):
        context.user_data["adm_mode"] = "mass_pick_status"
        from app.utils.keyboards import status_keyboard_with_prefix
        await reply_animated(
            update, context,
            "Выбери новый статус для нескольких заказов:",
            reply_markup=status_keyboard_with_prefix("mass:pick_status_id")
        )
        return

    # Заглушки для остальных функций
    if _is_text(text, ADMIN_MENU_ALIASES["admin_send"]):
        await reply_animated(update, context, "📣 Раздел «Рассылка» в разработке")
        return

    if _is_text(text, ADMIN_MENU_ALIASES["admin_addrs"]):
        await reply_animated(update, context, "📇 Раздел «Адреса» в разработке")
        return

    if _is_text(text, ADMIN_MENU_ALIASES["admin_reports"]):
        await reply_animated(update, context, "📊 Раздел «Отчёты» в разработке")
        return

    # Обработка режимов админки
    await _handle_admin_modes(update, context, raw_text)

async def _handle_admin_modes(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка различных режимов админки"""
    mode = context.user_data.get("adm_mode")
    
    if mode == "add_order_id":
        await _handle_add_order_id(update, context, raw_text)
    elif mode == "add_order_client":
        await _handle_add_order_client(update, context, raw_text)
    elif mode == "add_order_country":
        await _handle_add_order_country(update, context, raw_text)
    elif mode == "add_order_status":
        await _handle_add_order_status(update, context, raw_text)
    elif mode == "add_order_note":
        await _handle_add_order_note(update, context, raw_text)
    elif mode == "find_order":
        await _handle_find_order(update, context, raw_text)
    elif mode == "mass_update_status_ids":
        await _handle_mass_update_status(update, context, raw_text)
    else:
        logger.warning(f"Неизвестный режим админа: {mode}")
        await reply_animated(update, context, "Вы в админ-панели. Выберите действие:", reply_markup=ADMIN_MENU_KB)

async def _handle_add_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка ввода order_id при добавлении заказа"""
    context.user_data["adm_buf"] = {"order_id": raw_text}
    context.user_data["adm_mode"] = "add_order_client"
    await reply_animated(update, context, "Имя клиента (можно несколько @username):")

async def _handle_add_order_client(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка ввода клиента при добавлении заказа"""
    context.user_data["adm_buf"]["client_name"] = raw_text
    context.user_data["adm_mode"] = "add_order_country"
    await reply_animated(update, context, "Страна/склад (CN или KR):")

async def _handle_add_order_country(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка ввода страны при добавлении заказа"""
    country = raw_text.upper()
    if country not in ("CN", "KR"):
        await reply_animated(update, context, "Введи 'CN' (Китай) или 'KR' (Корея):")
        return
    
    context.user_data["adm_buf"]["country"] = country
    context.user_data["adm_mode"] = "add_order_status"
    await reply_animated(update, context, "Выбери стартовый статус кнопкой ниже или напиши точный:", 
                        reply_markup=status_keyboard(2))

async def _handle_add_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка ввода статуса при добавлении заказа"""
    if not is_valid_status(raw_text, STATUSES):
        await reply_animated(update, context, "Выбери статус кнопкой ниже или напиши точный:", 
                            reply_markup=status_keyboard(2))
        return
    
    context.user_data["adm_buf"]["status"] = raw_text.strip()
    context.user_data["adm_mode"] = "add_order_note"
    await reply_animated(update, context, "Примечание (или '-' если нет):")

async def _handle_add_order_note(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка ввода примечания и сохранение заказа"""
    buf = context.user_data.get("adm_buf", {})
    buf["note"] = raw_text if raw_text != "-" else ""
    
    try:
        # Создаем заказ
        order = Order(
            order_id=buf["order_id"],
            client_name=buf.get("client_name", ""),
            country=buf.get("country", ""),
            status=buf.get("status", "выкуплен"),
            note=buf.get("note", ""),
        )
        
        success = await OrderService.add_order(order)
        
        if success:
            # Добавляем участников
            usernames = extract_usernames(buf.get("client_name", ""))
            if usernames:
                await ParticipantService.ensure_participants(buf["order_id"], usernames)
            
            await reply_markdown_animated(update, context, f"✅ Заказ *{buf['order_id']}* добавлен")
        else:
            await reply_animated(update, context, "❌ Ошибка при добавлении заказа")
            
    except Exception as e:
        logger.error(f"Ошибка добавления заказа: {e}")
        await reply_animated(update, context, f"❌ Ошибка: {e}")
    finally:
        # Очищаем режим
        for key in ("adm_mode", "adm_buf"):
            context.user_data.pop(key, None)

async def _handle_find_order(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Поиск и отображение заказа"""
    parsed_id = extract_order_id(raw_text) or raw_text
    order = await OrderService.get_order(parsed_id)
    
    if not order:
        await reply_animated(update, context, "🙈 Заказ не найден.")
        context.user_data.pop("adm_mode", None)
        return

    # Формируем карточку заказа
    lines = [
        f"*order_id:* `{order.order_id}`",
        f"*client_name:* {order.client_name}",
        f"*status:* {order.status}",
        f"*note:* {order.note}",
        f"*country:* {order.country}",
    ]
    
    if order.origin and order.origin != order.country:
        lines.append(f"*origin:* {order.origin}")
    if order.updated_at:
        lines.append(f"*updated_at:* {order.updated_at}")

    await reply_markdown_animated(update, context, "\n".join(lines), reply_markup=order_card_kb(order.order_id))

    # Показываем участников
    participants = await ParticipantService.get_participants(order.order_id)
    part_text = build_participants_text(order.order_id, participants, 0, 8)
    kb = build_participants_kb(order.order_id, participants, 0, 8)
    
    await reply_markdown_animated(update, context, part_text, reply_markup=kb)
    context.user_data.pop("adm_mode", None)

async def _handle_mass_update_status(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Массовое обновление статусов"""
    # Парсим список order_id
    raw_ids = re.split(r"[,\s]+", raw_text.strip())
    ids = []
    seen = set()
    
    for token in raw_ids:
        oid = extract_order_id(token)
        if oid and oid not in seen:
            seen.add(oid)
            ids.append(oid)

    if not ids:
        await reply_animated(update, context, "Не нашёл order_id. Пришли ещё раз (пример: CN-1001 KR-2002).")
        return

    new_status = context.user_data.get("mass_status")
    if not new_status:
        await reply_animated(update, context, "Не выбран новый статус. Повтори с начала.")
        context.user_data.pop("adm_mode", None)
        return

    # Обновляем статусы
    ok, fail = 0, 0
    failed_ids = []
    
    for oid in ids:
        try:
            updated = await OrderService.update_order_status(oid, new_status)
            if updated:
                ok += 1
                # Уведомляем подписчиков
                try:
                    await notify_subscribers(context.application, oid, new_status)
                except Exception as e:
                    logger.warning(f"Ошибка уведомления подписчиков {oid}: {e}")
            else:
                fail += 1
                failed_ids.append(oid)
        except Exception as e:
            logger.error(f"Ошибка обновления статуса {oid}: {e}")
            fail += 1
            failed_ids.append(oid)

    # Очищаем режим
    context.user_data.pop("adm_mode", None)
    context.user_data.pop("mass_status", None)

    # Отчет
    parts = [
        "🧰 Массовая смена статусов — итог",
        f"Всего заказов: {len(ids)}",
        f"✅ Успешно: {ok}",
        f"❌ Ошибки: {fail}",
    ]
    
    if failed_ids:
        parts.append("")
        parts.append("Не удалось обновить:")
        parts.append(", ".join(failed_ids))
    
    await reply_animated(update, context, "\n".join(parts))

async def notify_subscribers(application, order_id: str, new_status: str):
    """Уведомление подписчиков об изменении статуса"""
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
            logger.warning(f"Не удалось уведомить {sub.user_id}: {e}")

def register(application):
    """Регистрация админских хэндлеров"""
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND) & filters.User(ADMIN_IDS), 
        handle_admin_text
    ))
    logger.info("✅ Админские хэндлеры зарегистрированы")
