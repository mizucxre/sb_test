import logging
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler

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

# Подменю «Рассылка»
BROADCAST_ALIASES = {
    "bc_all": {"📨 уведомления всем должникам", "уведомления всем должникам"},
    "bc_one": {"📩 уведомления по id разбора", "уведомления по id разбора"},
}

# Подменю «Адреса»
ADMIN_ADDR_ALIASES = {
    "export_addrs": {"📤 выгрузить адреса", "выгрузить адреса"},
    "edit_addr": {"✏️ изменить адрес по username", "изменить адрес по username"},
}

# Подменю «Отчёты»
REPORT_ALIASES = {
    "report_by_note": {"🧾 выгрузить разборы админа", "выгрузить разборы админа"},
    "report_unpaid": {"🧮 отчёт по должникам", "отчёт по должникам"},
}

def _is_text(text: str, group: set[str]) -> bool:
    """Проверка соответствия текста группе алиасов"""
    return text.strip().lower() in {x.lower() for x in group}

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin - вход в админ-панель"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    
    # Очищаем состояние
    for key in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id", "mass_status"):
        context.user_data.pop(key, None)
    
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений админа"""
    user_id = update.effective_user.id
    if not _is_admin(user_id, ADMIN_IDS):
        logger.warning(f"❌ Неадмин {user_id} попытался использовать админский обработчик")
        return

    raw_text = (update.message.text or "").strip()
    text = raw_text.lower()

    logger.info(f"🛠 Админский обработчик: сообщение от {user_id}: '{raw_text}'")

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

    # Подменю "Рассылка"
    if _is_text(text, ADMIN_MENU_ALIASES["admin_send"]):
        from app.utils.keyboards import BROADCAST_MENU_KB
        await reply_animated(update, context, "📣 Раздел «Рассылка»", reply_markup=BROADCAST_MENU_KB)
        return

    # Подменю "Адреса"
    if _is_text(text, ADMIN_MENU_ALIASES["admin_addrs"]):
        from app.utils.keyboards import ADMIN_ADDR_MENU_KB
        await reply_animated(update, context, "📇 Раздел «Адреса»", reply_markup=ADMIN_ADDR_MENU_KB)
        return

    # Подменю "Отчёты"
    if _is_text(text, ADMIN_MENU_ALIASES["admin_reports"]):
        from app.utils.keyboards import REPORTS_MENU_KB
        await reply_animated(update, context, "📊 Раздел «Отчёты»", reply_markup=REPORTS_MENU_KB)
        return

    # Обработка подменю "Рассылка"
    if _is_text(text, BROADCAST_ALIASES["bc_all"]):
        await broadcast_all_unpaid_text(update, context)
        return

    if _is_text(text, BROADCAST_ALIASES["bc_one"]):
        context.user_data["adm_mode"] = "adm_remind_unpaid_order"
        await reply_markdown_animated(update, context, "✉️ Введи *order_id* для рассылки неплательщикам:")
        return

    # Обработка подменю "Адреса"
    if _is_text(text, ADMIN_ADDR_ALIASES["export_addrs"]):
        context.user_data["adm_mode"] = "adm_export_addrs"
        await reply_animated(update, context, "Пришли список @username (через пробел/запятую/новые строки):")
        return

    if _is_text(text, ADMIN_ADDR_ALIASES["edit_addr"]):
        context.user_data["adm_mode"] = "adm_edit_addr_username"
        await reply_animated(update, context, "Пришли @username пользователя, чей адрес нужно изменить:")
        return

    # Обработка подменю "Отчёты"
    if _is_text(text, REPORT_ALIASES["report_by_note"]):
        context.user_data["adm_mode"] = "adm_export_orders_by_note"
        await reply_markdown_animated(update, context, "🧾 Пришли метку/слово из *note*, по которому помечены твои разборы:")
        return

    if _is_text(text, REPORT_ALIASES["report_unpaid"]):
        await report_unpaid(update, context)
        return

    # Обработка режимов админки
    await _handle_admin_modes(update, context, raw_text)

async def _handle_admin_modes(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка различных режимов админки"""
    mode = context.user_data.get("adm_mode")
    
    logger.info(f"🛠 Админский обработчик: режим '{mode}', текст: '{raw_text}'")
    
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
    elif mode == "adm_remind_unpaid_order":
        await _handle_remind_unpaid_order(update, context, raw_text)
    elif mode == "adm_export_addrs":
        await _handle_export_addresses(update, context, raw_text)
    elif mode == "adm_export_orders_by_note":
        await _handle_export_orders_by_note(update, context, raw_text)
    else:
        logger.warning(f"❌ Админский обработчик: неизвестный режим админа: {mode}")
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

async def _handle_remind_unpaid_order(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Обработка напоминания по одному заказу"""
    parsed_id = extract_order_id(raw_text) or raw_text
    order = await OrderService.get_order(parsed_id)
    
    if not order:
        await reply_animated(update, context, "🙈 Заказ не найден.")
        return

    # Получаем неплательщиков
    usernames = await ParticipantService.get_unpaid_usernames(parsed_id)
    if not usernames:
        await reply_animated(update, context, f"🎉 По заказу *{parsed_id}* должников нет!")
        context.user_data.pop("adm_mode", None)
        return

    # Рассылаем напоминания
    success_count = 0
    fail_count = 0
    report_lines = [f"📩 Уведомления по заказу {parsed_id}:"]

    for username in usernames:
        try:
            # Получаем user_id по username
            user_ids = await AddressService.get_user_ids_by_usernames([username])
            if not user_ids:
                fail_count += 1
                report_lines.append(f"❌ @{username} - не найден в адресах")
                continue

            user_id = user_ids[0]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"💳 Напоминание по разбору *{parsed_id}*\n"
                        f"Статус: *Доставка не оплачена*\n\n"
                        f"Пожалуйста, оплатите доставку. Если уже оплатили — можно игнорировать."
                    ),
                    parse_mode="Markdown",
                )
                success_count += 1
                report_lines.append(f"✅ @{username}")
            except Exception as e:
                fail_count += 1
                report_lines.append(f"❌ @{username} - ошибка отправки: {str(e)}")

        except Exception as e:
            fail_count += 1
            report_lines.append(f"❌ @{username} - ошибка: {str(e)}")

    report_lines.append(f"\nИтого: ✅ {success_count} ❌ {fail_count}")
    await reply_animated(update, context, "\n".join(report_lines))
    context.user_data.pop("adm_mode", None)

async def _handle_export_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Экспорт адресов по username"""
    usernames = extract_usernames(raw_text)
    if not usernames:
        await reply_animated(update, context, "Не найдено @username в сообщении.")
        context.user_data.pop("adm_mode", None)
        return

    addresses = await AddressService.get_addresses_by_usernames(usernames)
    if not addresses:
        await reply_animated(update, context, "Адреса не найдены.")
        context.user_data.pop("adm_mode", None)
        return

    lines = []
    for addr in addresses:
        lines.append(
            f"@{addr.username}\n"
            f"ФИО: {addr.full_name}\n"
            f"Телефон: {addr.phone}\n"
            f"Город: {addr.city}\n"
            f"Адрес: {addr.address}\n"
            f"Индекс: {addr.postcode}\n"
            "—"
        )

    await reply_animated(update, context, "\n".join(lines))
    context.user_data.pop("adm_mode", None)

async def _handle_export_orders_by_note(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Экспорт заказов по метке в примечании"""
    marker = raw_text.strip()
    if not marker:
        await reply_animated(update, context, "Пришли метку/слово для поиска в note.")
        return

    orders = await OrderService.get_orders_by_note(marker)
    if not orders:
        await reply_animated(update, context, "Ничего не найдено.")
    else:
        lines = []
        for order in orders:
            lines.append(
                f"*order_id:* `{order.order_id}`\n"
                f"*client_name:* {order.client_name}\n"
                f"*phone:* {order.phone or '-'}\n"
                f"*origin:* {order.origin or '-'}\n"
                f"*status:* {order.status}\n"
                f"*note:* {order.note or '-'}\n"
                f"*country:* {order.country}\n"
                f"*updated_at:* {order.updated_at or '-'}\n"
                "—"
            )
        await reply_markdown_animated(update, context, "\n".join(lines))
    context.user_data.pop("adm_mode", None)

async def broadcast_all_unpaid_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка всем должникам"""
    try:
        grouped = await ParticipantService.get_all_unpaid_grouped()
        if not grouped:
            await reply_animated(update, context, "🎉 Должников не найдено — красота!")
            return

        total_ok = 0
        total_fail = 0
        report_lines = ["📣 Уведомления всем должникам:"]

        for order_id, usernames in grouped.items():
            order_ok = 0
            order_fail = 0
            report_lines.append(f"\n{order_id}:")

            for username in usernames:
                try:
                    user_ids = await AddressService.get_user_ids_by_usernames([username])
                    if not user_ids:
                        order_fail += 1
                        report_lines.append(f"❌ @{username} - не найден")
                        continue

                    user_id = user_ids[0]
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"💳 Напоминание по разбору *{order_id}*\n"
                                f"Статус: *Доставка не оплачена*\n\n"
                                f"Пожалуйста, оплатите доставку. Если уже оплатили — можно игнорировать."
                            ),
                            parse_mode="Markdown",
                        )
                        order_ok += 1
                        report_lines.append(f"✅ @{username}")
                    except Exception as e:
                        order_fail += 1
                        report_lines.append(f"❌ @{username} - ошибка отправки")
                except Exception:
                    order_fail += 1
                    report_lines.append(f"❌ @{username} - ошибка")

            total_ok += order_ok
            total_fail += order_fail
            report_lines.append(f"Итого по разбору: ✅ {order_ok} ❌ {order_fail}")

        report_lines.insert(1, f"\nВсего разборов: {len(grouped)}")
        report_lines.append(f"\nОбщий итог: ✅ {total_ok} ❌ {total_fail}")

        await reply_animated(update, context, "\n".join(report_lines))

    except Exception as e:
        logger.error(f"Ошибка массовой рассылки: {e}")
        await reply_animated(update, context, f"❌ Ошибка при рассылке: {e}")

async def report_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отчет по должникам"""
    try:
        grouped = await ParticipantService.get_all_unpaid_grouped()
        if not grouped:
            await reply_animated(update, context, "🎉 Должников не найдено — красота!")
            return

        lines = ["📋 Отчёт по должникам:"]
        for order_id, usernames in grouped.items():
            user_list = ", ".join([f"@{u}" for u in usernames]) if usernames else "—"
            lines.append(f"• {order_id}: {user_list}")

        await reply_animated(update, context, "\n".join(lines))
    except Exception as e:
        logger.error(f"Ошибка формирования отчета: {e}")
        await reply_animated(update, context, f"❌ Ошибка при формировании отчета: {e}")

async def notify_subscribers(application, order_id: str, new_status: str):
    """Уведомление подписчиков об изменении статуса"""
    try:
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
    except Exception as e:
        logger.error(f"Ошибка уведомления подписчиков: {e}")

def register(application):
    """Регистрация админских хэндлеров (ТОЛЬКО КОМАНДЫ)"""
    application.add_handler(CommandHandler("admin", admin_menu))
    # MessageHandler удален - теперь обрабатывается в text_handler.py
    logger.info("✅ Админские хэндлеры зарегистрированы (только команды)")
