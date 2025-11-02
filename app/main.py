# -*- coding: utf-8 -*-
# SEABLUU bot — main.py (fixed v2)

import logging
import re
import asyncio
from typing import List, Dict, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ContextTypes,
)
from telegram.constants import ChatAction

from . import sheets
from .config import ADMIN_IDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

USERNAME_TOKEN_RE = re.compile(r"@?[A-Za-z0-9_]{5,}")
ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—_]*([A-Z0-9]{2,})", re.IGNORECASE)


def _normalize_username(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


def _looks_like_username(tok: str) -> bool:
    t = (tok or "").strip()
    if not t:
        return False
    if t.startswith("@"):
        return True
    if USERNAME_TOKEN_RE.fullmatch(t) and not extract_order_id(t):
        digits = re.sub(r"\D+", "", t)
        return len(digits) < 6
    return False


def extract_order_id(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    m = ORDER_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2).upper()}"
    if "-" in s:
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and left.replace("_", "").isalpha():
            right_norm = re.sub(r"[^A-Z0-9]+", "", right, flags=re.I)
            if right_norm:
                return f"{left.upper()}-{right_norm.upper()}"
    return None


def _is_admin(uid) -> bool:
    return uid in ADMIN_IDS or str(uid) in {str(x) for x in ADMIN_IDS}


def normalize_status(raw: str) -> str:
    if not raw:
        return "—"
    s = str(raw)
    m = re.search(r'(?:^|:)pick_status_id:?([0-9]+)$', s)
    if m:
        try:
            i = int(m.group(1))
            if 0 <= i < len(STATUSES):
                return STATUSES[i]
        except Exception:
            pass
    if s.startswith('adm:pick_status_id'):
        try:
            i = int(re.sub(r'[^0-9]', '', s))
            if 0 <= i < len(STATUSES):
                return STATUSES[i]
        except Exception:
            pass
    return s


async def _typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float = 0.6):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)


async def reply_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id, 0.4)
    return await msg.reply_text(text, **kwargs)


async def reply_markdown_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id, 0.4)
    return await msg.reply_markdown(text, **kwargs)


async def show_loader(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "⏳ Загрузка…"):
    msg = update.message or update.callback_query.message
    try:
        return await msg.reply_text(text)
    except Exception:
        return None


async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, message):
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


BTN_TRACK_NEW = "🔍 Отследить разбор"
BTN_ADDRS_NEW = "🏠 Мои адреса"
BTN_SUBS_NEW  = "🔔 Мои подписки"
BTN_PROFILE_NEW = "👤 Профиль"
BTN_CANCEL_NEW = "❌ Отмена"

CLIENT_ALIASES = {
    "track": {BTN_TRACK_NEW, "отследить разбор"},
    "addrs": {BTN_ADDRS_NEW, "мои адреса"},
    "subs":  {BTN_SUBS_NEW,  "мои подписки"},
    "profile": {BTN_PROFILE_NEW, "профиль"},
    "cancel": {BTN_CANCEL_NEW, "отмена", "cancel"},
}

BTN_ADMIN_ADD_NEW     = "➕ Добавить разбор"
BTN_ADMIN_TRACK_NEW   = "🔎 Поиск"
BTN_ADMIN_SEND_NEW    = "📣 Админ: Рассылка"
BTN_ADMIN_ADDRS_NEW   = "👤 Клиенты"
BTN_ADMIN_REPORTS_NEW = "📊 Отчёты"
BTN_ADMIN_MASS_NEW    = "🧰 Массовая смена статусов"
BTN_ADMIN_EXIT_NEW    = "🚪 Выйти из админ-панели"

BTN_BACK_TO_ADMIN_NEW = "⬅️ Назад, в админ-панель"

ADMIN_MENU_ALIASES = {
    "admin_add": {BTN_ADMIN_ADD_NEW, "добавить разбор"},
    "admin_track": {BTN_ADMIN_TRACK_NEW, "отследить разбор", "поиск"},
    "admin_send": {BTN_ADMIN_SEND_NEW, "админ: рассылка"},
    "admin_addrs": {BTN_ADMIN_ADDRS_NEW, "клиенты"},
    "admin_reports": {BTN_ADMIN_REPORTS_NEW, "отчёты"},
    "admin_mass": {BTN_ADMIN_MASS_NEW, "массовая смена статусов"},
    "admin_exit": {BTN_ADMIN_EXIT_NEW, "выйти из админ-панели"},
    "back_admin": {BTN_BACK_TO_ADMIN_NEW, "назад, в админ-панель"},
}

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK_NEW)],
        [KeyboardButton(BTN_ADDRS_NEW), KeyboardButton(BTN_SUBS_NEW)],
        [KeyboardButton(BTN_PROFILE_NEW)],
        [KeyboardButton(BTN_CANCEL_NEW)],
    ],
    resize_keyboard=True,
)

ADMIN_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ADMIN_ADD_NEW),  KeyboardButton(BTN_ADMIN_TRACK_NEW)],
        [KeyboardButton(BTN_ADMIN_SEND_NEW), KeyboardButton(BTN_ADMIN_ADDRS_NEW)],
        [KeyboardButton(BTN_ADMIN_REPORTS_NEW), KeyboardButton(BTN_ADMIN_MASS_NEW)],
        [KeyboardButton(BTN_ADMIN_EXIT_NEW)],
    ],
    resize_keyboard=True,
)

FIND_EXPECTING_QUERY_FLAG = "find_expect_query"
FIND_RESULTS_KEY = "find_results"
FIND_PAGE_KEY = "find_page"


def _build_find_results_kb(items: List[Dict], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    start = page * per_page
    chunk = items[start:start+per_page]
    rows = []
    for o in chunk:
        oid = str(o.get("order_id", "")).strip()
        if not oid:
            continue
        rows.append([InlineKeyboardButton(f"📦 {oid}", callback_data=f"find:open:{oid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀︎", callback_data=f"find:page:{page-1}"))
    if start + per_page < len(items):
        nav.append(InlineKeyboardButton("▶︎", callback_data=f"find:page:{page+1}"))
    if nav:
        rows.append(nav)
    if items:
        rows.append([InlineKeyboardButton("✏️ Изменить статус всем найденным", callback_data="find:bulk:ask")])
    return InlineKeyboardMarkup(rows)


async def _render_found_cards(update: Update, context: ContextTypes.DEFAULT_TYPE, orders: List[Dict]):
    if not orders:
        return await reply_animated(update, context, "Нет карточек.")

    def flag(country: str) -> str:
        c = (country or "").upper()
        return "🇨🇳" if c == "CN" else "🇰🇷" if c == "KR" else "🏳️"

    max_len = max(len(str(o.get("order_id", ""))) for o in orders)
    lines = ["🔎 Найденные заказы:"]

    for o in orders:
        oid = str(o.get("order_id", "")).strip()
        status = normalize_status(o.get("status"))
        origin = (o.get("origin") or o.get("country") or "—").upper()
        dt_iso = (o.get("updated_at", "") or "").replace("T", " ")
        dt_short = dt_iso[11:16] if len(dt_iso) >= 16 else "--:--"
        client = o.get("client_name") or "—"
        part = sheets.get_participants(oid) or []
        unpaid = sum(1 for p in part if not p.get("paid"))

        lines.append(
            f"{oid.ljust(max_len)} · {status} · {flag(origin)} {origin} · {dt_short} · клиенты: {client} · долги: {unpaid}"
        )

    await reply_animated(update, context, "\n".join(lines))


async def _open_order_card(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        return await reply_animated(update, context, "🙈 Заказ не найден.")

    def flag(c: str) -> str:
        c = (c or "").upper()
        return "🇨🇳" if c == "CN" else "🇰🇷" if c == "KR" else "🏳️"

    st = normalize_status(order.get("status", "—"))
    orig = (order.get("origin") or order.get("country") or "—").upper()
    dt = (order.get("updated_at","") or "").replace("T"," ")
    note = order.get("note", "—")

    head_lines = [
        f"📦 {order_id}",
        f"Статус: {st}",
        f"Страна: {flag(orig)} {orig}",
        f"Обновлено: {dt or '—'}",
    ]
    if note and note != "—":
        head_lines.append(f"Заметка: {note}")

    await reply_animated(update, context, "\n".join(head_lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Изменить статус", callback_data=f"adm:status_menu:{order_id}")]]))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hello = (
        "✨ Привет! Я *SEABLUU* Helper — помогу отследить разборы, адреса и подписки.\n\n"
        "• 🔍 Отследить разбор — статус по `order_id` (например, `CN-12345`).\n"
        "• 🔔 Подписки — уведомлю, когда статус заказа изменится.\n"
        "• 🏠 Мои адреса — сохраню/обновлю адрес для доставки.\n"
        "• 👤 Профиль — ваши данные и связанные разборы.\n\n"
        "Если что-то пошло не так — нажми «Отмена» или используй /help."
    )
    await reply_markdown_animated(update, context, hello, reply_markup=MAIN_KB)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_animated(
        update, context,
        "📘 Помощь:\n"
        "• 🔍 Отследить разбор — статус по номеру\n"
        "• 🏠 Мои адреса — добавить/изменить адрес\n"
        "• 🔔 Мои подписки — список подписок\n"
        "• 👤 Профиль — общая информация\n"
        "• /admin — админ-панель (для админов)"
    )


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    for k in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id"):
        context.user_data.pop(k, None)
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    if _is_admin(update.effective_user.id) and _looks_like_username(raw) and not context.user_data.get(FIND_EXPECTING_QUERY_FLAG):
        loader = await show_loader(update, context, "⏳ Ищу по username…")
        try:
            orders = sheets.get_orders_by_username(raw)
            if not orders:
                await reply_animated(update, context, "Ничего не нашёл по этому username.")
            else:
                await _render_found_cards(update, context, orders)
                kb = _build_find_results_kb(orders, page=0)
                await reply_markdown_animated(update, context, f"Найдено заказов: *{len(orders)}*.", reply_markup=kb)
        finally:
            await safe_delete_message(context, loader)
        return

    if context.user_data.get(FIND_EXPECTING_QUERY_FLAG) and text == BTN_BACK_TO_ADMIN_NEW.lower():
        context.user_data.pop(FIND_EXPECTING_QUERY_FLAG, None)
        await admin_menu(update, context)
        return

    if context.user_data.get(FIND_EXPECTING_QUERY_FLAG):
        context.user_data.pop(FIND_EXPECTING_QUERY_FLAG, None)
        loader = await show_loader(update, context, "⏳ Ищу…")
        try:
            tokens = [t for t in re.split(r"[,\s]+", raw) if t.strip()]
            if not tokens:
                return await reply_animated(update, context, "Пусто. Пришлите order_id / @username / телефон.")
            orders: List[Dict] = []
            seen = set()

            for t in tokens:
                oid = extract_order_id(t)
                if oid and oid not in seen:
                    od = sheets.get_order(oid)
                    if od:
                        orders.append(od); seen.add(oid)

            for t in tokens:
                if _looks_like_username(t):
                    for od in sheets.get_orders_by_username(t):
                        oid = str(od.get("order_id", "")).strip()
                        if oid and oid not in seen:
                            orders.append(od); seen.add(oid)

            for t in tokens:
                if len(re.sub(r"\D+","",t)) >= 6 and not t.startswith("@") and not extract_order_id(t):
                    for od in sheets.get_orders_by_phone(t):
                        oid = str(od.get("order_id", "")).strip()
                        if oid and oid not in seen:
                            orders.append(od); seen.add(oid)

            if not orders:
                return await reply_animated(update, context, "Ничего не нашёл по запросу.")

            context.user_data[FIND_RESULTS_KEY] = [ {"order_id": o.get("order_id","")} for o in orders ]
            context.user_data[FIND_PAGE_KEY] = 0

            await _render_found_cards(update, context, orders)
            kb = _build_find_results_kb(orders, page=0)
            await reply_markdown_animated(update, context, f"Найдено заказов: *{len(orders)}*. Выберите:", reply_markup=kb)
        finally:
            await safe_delete_message(context, loader)
        return

    if _is_admin(update.effective_user.id):
        if text in {x.lower() for x in ADMIN_MENU_ALIASES["admin_exit"]}:
            context.user_data.clear()
            await reply_animated(update, context, "🚪 Готово, вышли из админ-панели.", reply_markup=MAIN_KB)
            return

        if text in {x.lower() for x in ADMIN_MENU_ALIASES["admin_add"]}:
            context.user_data["adm_mode"] = "add_order_id"
            context.user_data["adm_buf"] = {}
            await reply_markdown_animated(update, context, "➕ Введи *order_id* (например: `CN-12345`):", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK_TO_ADMIN_NEW)]], resize_keyboard=True))
            return

        if text in {x.lower() for x in ADMIN_MENU_ALIASES["admin_reports"]}:
            await reply_animated(update, context, "📊 Раздел «Отчёты»", reply_markup=ADMIN_MENU_KB)
            return

        if text in {x.lower() for x in ADMIN_MENU_ALIASES["admin_track"]}:
            context.user_data[FIND_EXPECTING_QUERY_FLAG] = True
            await reply_markdown_animated(update, context, (
                "🔎 *Поиск заказов*\n"
                "Пришлите *одно или несколько* значений (можно смешивать):\n"
                "• `order_id` (например, CN-12345)\n"
                "• `@username`\n"
                "• телефон (в любом формате)\n\n"
                "Разделяйте пробелами, запятыми или с новой строки."
            ), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK_TO_ADMIN_NEW)]], resize_keyboard=True))
            return

    if text in {x.lower() for x in CLIENT_ALIASES["cancel"]}:
        context.user_data["mode"] = None
        await reply_animated(update, context, "Отменили действие. Что дальше? 🙂", reply_markup=MAIN_KB)
        return

    if text in {x.lower() for x in CLIENT_ALIASES["track"]}:
        context.user_data["mode"] = "track"
        await reply_animated(update, context, "🔎 Отправьте номер заказа (например: CN-12345):")
        return

    if text in {x.lower() for x in CLIENT_ALIASES["addrs"]}:
        context.user_data["mode"] = None
        await show_addresses(update, context)
        return

    if text in {x.lower() for x in CLIENT_ALIASES["subs"]}:
        context.user_data["mode"] = None
        await show_subscriptions(update, context)
        return

    if text in {x.lower() for x in CLIENT_ALIASES["profile"]}:
        await show_profile(update, context)
        return

    mode = context.user_data.get("mode")
    if mode == "track":
        await query_status(update, context, raw)
        return

    if _is_admin(update.effective_user.id):
        await reply_animated(update, context, "Вы в админ-панели. Выберите действие:", reply_markup=ADMIN_MENU_KB)
    else:
        await reply_animated(update, context, "Хмм, не понял. Выберите кнопку ниже или введите номер заказа. Если что — «Отмена».", reply_markup=MAIN_KB)


async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    await _typing(context, update.effective_chat.id, 0.5)
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        await reply_animated(update, context, "🙈 Такой заказ не найден. Проверьте номер или повторите позже.")
        return
    status = normalize_status(order.get("status")) or "статус не указан"
    origin = order.get("origin") or ""
    txt = f"📦 Заказ *{order_id}*\nСтатус: *{status}*"
    if origin:
        txt += f"\nСтрана/источник: {origin}"

    if sheets.is_subscribed(update.effective_user.id, order_id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]])
    await reply_markdown_animated(update, context, txt, reply_markup=kb)
    context.user_data["mode"] = None


async def show_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context, update.effective_chat.id, 0.4)
    addrs = sheets.list_addresses(update.effective_user.id)
    if not addrs:
        await reply_animated(
            update, context,
            "У вас пока нет адреса. Добавим?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить адрес", callback_data="addr:add")]]),
        )
        return
    lines = []
    for a in addrs:
        lines.append(f"• {a['full_name']} — {a['phone']}\n{a['city']}, {a['address']}, {a['postcode']}")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
            [InlineKeyboardButton("🗑 Удалить адрес", callback_data="addr:del")],
        ]
    )
    await reply_animated(update, context, "📍 Ваш адрес доставки:\n" + "\n\n".join(lines), reply_markup=kb)


async def save_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    sheets.upsert_address(
        user_id=u.id,
        username=u.username or "",
        full_name=context.user_data.get("full_name", ""),
        phone=context.user_data.get("phone", ""),
        city=context.user_data.get("city", ""),
        address=context.user_data.get("address", ""),
        postcode=context.user_data.get("postcode", ""),
    )
    try:
        if u.username:
            for oid in sheets.find_orders_for_username(u.username):
                try:
                    sheets.subscribe(u.id, oid)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"auto-subscribe failed: {e}")

    context.user_data["mode"] = None
    msg = (
        "✅ Адрес сохранён!\n\n"
        f"👤 ФИО: {context.user_data.get('full_name','')}\n"
        f"📞 Телефон: {context.user_data.get('phone','')}\n"
        f"🏙 Город: {context.user_data.get('city','')}\n"
        f"🏠 Адрес: {context.user_data.get('address','')}\n"
        f"📮 Индекс: {context.user_data.get('postcode','')}"
    )
    await reply_animated(update, context, msg, reply_markup=MAIN_KB)


async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context, update.effective_chat.id, 0.4)
    subs = sheets.list_subscriptions(update.effective_user.id)
    if not subs:
        await reply_animated(update, context, "Пока нет подписок. Отследите заказ и нажмите «Подписаться».")
        return
    txt_lines, kb_rows = [], []
    for s in subs:
        last = s.get("last_sent_status", "—")
        order_id = s["order_id"]
        txt_lines.append(f"• {order_id} — последний статус: {last}")
        kb_rows.append([InlineKeyboardButton(f"🗑 Отписаться от {order_id}", callback_data=f"unsub:{order_id}")])
    await reply_animated(update, context, "🔔 Ваши подписки:\n" + "\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb_rows))


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    addresses = sheets.list_addresses(u.id)
    addr = addresses[0] if addresses else {}

    orders = sheets.orders_for_username(u.username or "") if (u.username) else []
    order_lines = []
    for oid, st in orders[:10]:
        order_lines.append(f"• {oid} — {normalize_status(st)}")
    more = ("\n… и ещё " + str(len(orders) - 10)) if len(orders) > 10 else ""

    text = (
        f"👤 Профиль - @{(u.username or '').lower()}\n\n"
        f"Имя - {((u.first_name or '') + ' ' + (u.last_name or '')).strip()}\n\n"
        "Ваши данные:\n"
        f"ФИО: {addr.get('full_name', '—')}\n"
        f"Телефон: {addr.get('phone', '—')}\n"
        f"Город: {addr.get('city', '—')}\n"
        f"Адрес: {addr.get('address', '—')}\n"
        f"Индекс: {addr.get('postcode', '—')}\n\n"
        "Ваши разборы:\n"
        + ("\n".join(order_lines) if order_lines else "—")
        + more
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
        [InlineKeyboardButton("🔔 Мои подписки", callback_data="client:subs")],
    ])

    await reply_animated(update, context, text, reply_markup=kb)


async def notify_subscribers(application, order_id: str, new_status: str):
    try:
        subs_all = sheets.get_all_subscriptions()
        targets = [s for s in subs_all if str(s.get("order_id")) == str(order_id)]
    except Exception:
        usernames = sheets.get_unpaid_usernames(order_id) + [p.get("username") for p in sheets.get_participants(order_id)]
        user_ids = list(set(sheets.get_user_ids_by_usernames([u for u in usernames if u])))
        targets = [{"user_id": uid, "order_id": order_id} for uid in user_ids]

    for s in targets:
        uid = int(s["user_id"])
        try:
            await application.bot.send_message(
                chat_id=uid,
                text=f"🔄 Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                parse_mode="Markdown",
            )
            try:
                sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"notify_subscribers fail to {uid}: {e}")


async def _finalize_new_order(update: Update, context: ContextTypes.DEFAULT_TYPE, status_text: str):
    buf = context.user_data.get("adm_buf") or {}
    order_id = buf.get("order_id")
    country  = (buf.get("country") or "").upper()
    client_name_raw = buf.get("client_name", "").strip()

    if not order_id or country not in ("CN", "KR"):
        await reply_animated(update, context, "⚠️ Не хватает данных для создания разбора. Начните заново.")
        context.user_data.pop("adm_mode", None)
        return

    sheets.add_order({
        "order_id": order_id,
        "client_name": client_name_raw,
        "origin": country,
        "status": status_text,
    })

    usernames: List[str] = []
    if client_name_raw:
        for tok in re.split(r"[\s,]+", client_name_raw):
            tok = tok.strip()
            if tok.startswith("@"):
                tok = tok[1:]
            if tok:
                usernames.append(tok)

    if usernames:
        sheets.ensure_participants(order_id, usernames)
        sheets.ensure_clients_from_usernames(usernames)
        ids = sheets.get_user_ids_by_usernames(usernames)
        sent = 0
        for uid in ids:
            try:
                sheets.subscribe(uid, order_id)
            except Exception:
                pass
            try:
                await context.application.bot.send_message(
                    chat_id=uid,
                    text=(f"🆕 Создан новый разбор: {order_id}\nСтатус: {status_text}\nСтрана: {country}")
                )
                sent += 1
            except Exception:
                pass
        await reply_animated(update, context, f"✅ Разбор {order_id} создан. Уведомлений: {sent}")
    else:
        await reply_animated(update, context, f"✅ Разбор {order_id} создан.")

    context.user_data.pop("adm_mode", None)
    context.user_data.pop("adm_buf", None)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    if data.startswith("sub:"):
        order_id = data.split(":",1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]]))
        return
    if data.startswith("unsub:"):
        order_id = data.split(":",1)[1]
        sheets.unsubscribe(update.effective_user.id, order_id)
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]]))
        return

    if data == "addr:add":
        await q.message.reply_text("👤 ФИО:")
        context.user_data["mode"] = "add_address_fullname"
        return
    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await q.message.reply_text("🗑 Адрес удалён" if ok else "Адресов не было")
        return

    if data.startswith("find:open:"):
        oid = data.split(":",2)[2]
        await _open_order_card(update, context, oid)
        return
    if data.startswith("find:page:"):
        page = int(data.split(":",2)[2])
        items = context.user_data.get(FIND_RESULTS_KEY) or []
        kb = _build_find_results_kb(items, page=page)
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text("Страница обновлена.", reply_markup=kb)
        return


from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

__all__ = [
    "register_handlers",
    "start", "help_cmd", "admin_menu",
    "handle_text", "on_callback",
]
