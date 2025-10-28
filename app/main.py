
# -*- coding: utf-8 -*-
# SEABLUU bot — patched main.py implementing:
# - Admin "Поиск" with multi tokens + full cards + "Изменить статус всем найденным"
# - Admin "Клиенты": text export + list/search with pagination
# - Broadcast by multiple order_id
# - Reports: "Последние разборы"
# - Mass status change robust parsing (supports CN-TEST / KR-FREE)
# - Notify clients from client_name on order create
# - Loader "Загрузка…" messages
# - Client "Профиль"
# NOTE: This file is crafted as a drop-in replacement for app/main.py.
# It keeps public function names commonly used by webhook/router: start, help_cmd,
# admin_menu, handle_text, on_callback. If your webhook registers other handlers,
# add them similarly here.

import logging
import re
import asyncio
from typing import List, Tuple, Dict, Any, Optional

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

# ---------------------- Константы и утилиты ----------------------

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

ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \\-–—_]*([A-Z0-9]{2,})", re.IGNORECASE)
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,})")

def extract_order_id(s: str) -> Optional[str]:
    """Normalize input token to ORDER_ID format PREFIX-SUFFIX.
    Supports alpha suffixes like CN-TEST and KR-FREE; cleans junk chars.
    """
    if not s:
        return None
    s = s.strip()
    m = ORDER_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2).upper()}"
    # fallback: PREFIX-SUFFIX already present
    if "-" in s:
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and left.replace("_","").replace(" ","").isalpha():
            right_norm = re.sub(r"[^A-Z0-9]+", "", right, flags=re.I)
            if right_norm:
                return f"{left.upper()}-{right_norm.upper()}"
    return None

def is_valid_status(s: str, statuses: List[str]) -> bool:
    return bool(s) and s.strip().lower() in {x.lower() for x in statuses}

def _is_admin(uid) -> bool:
    return uid in ADMIN_IDS or str(uid) in {str(x) for x in ADMIN_IDS}

# -------- небольшая «анимация»/лоадер --------

async def _typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float = 0.6):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)

async def reply_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    # normalize literal "\n" (двойной слэш в коде) к реальным переводам строк
    if isinstance(text, str):
        text = text.replace('\\n', '\n')
    await _typing(context, msg.chat_id, 0.4)
    return await msg.reply_text(text, **kwargs)

async def reply_markdown_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    # normalize literal "\n" (двойной слэш в коде) к реальным переводам строк
    if isinstance(text, str):
        text = text.replace('\\n', '\n')
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

# ======= /find: МНОГОКРИТЕРИАЛЬНЫЙ поиск (order_id / @username / телефон) =======
FIND_EXPECTING_QUERY_FLAG = "find_expect_query"  # ключ в context.user_data
FIND_RESULTS_KEY = "find_results"
FIND_PAGE_KEY = "find_page"

def _guess_query_type(q: str) -> str:
    """
    Возвращает один из: 'order_id' / 'username' / 'phone'
    """
    q = (q or "").strip()
    if not q:
        return "order_id"
    if q.startswith("@"):
        return "username"
    # order_id вида AA-12345 (буквы-цифры с дефисом)
    if "-" in q:
        left, right = q.split("-", 1)
        if left and right and left.strip().isalpha():
            return "order_id"
    # иначе считаем телефоном, если много цифр
    digits = re.sub(r"\\D+", "", q)
    if len(digits) >= 6:
        return "phone"
    return "order_id"

async def admin_find_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /find или кнопка «Поиск»: просим ввести *несколько* значений."""
    uid = update.effective_user.id
    if not _is_admin(uid):
        return await reply_animated(update, context, "Доступно только администраторам.")
    context.user_data[FIND_EXPECTING_QUERY_FLAG] = True
    text = (
        "🔎 *Поиск заказов*\\n"
        "Пришлите *одно или несколько* значений (можно смешивать):\\n"
        "• `order_id` (например, CN-12345)\\n"
        "• `@username`\\n"
        "• телефон (в любом формате)\\n\\n"
        "Разделяйте пробелами, запятыми или с новой строки."
    )
    return await reply_markdown_animated(update, context, text, reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK_TO_ADMIN_NEW)]], resize_keyboard=True))

def _build_find_results_kb(items: List[Dict], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    start = page * per_page
    chunk = items[start:start+per_page]
    rows = []
    for o in chunk:
        oid = str(o.get("order_id", "")).strip()
        if not oid:
            continue
        rows.append([InlineKeyboardButton(f"📦 {oid}", callback_data=f"find:open:{oid}")])
    # пагинация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀︎", callback_data=f"find:page:{page-1}"))
    if start + per_page < len(items):
        nav.append(InlineKeyboardButton("▶︎", callback_data=f"find:page:{page+1}"))
    if nav:
        rows.append(nav)
    # нижняя «массовая смена»
    if items:
        rows.append([InlineKeyboardButton("✏️ Изменить статус всем найденным", callback_data="find:bulk:ask")])
    return InlineKeyboardMarkup(rows)


async def _render_found_cards(update: Update, context: ContextTypes.DEFAULT_TYPE, orders: List[Dict]):
    """Вывести краткие карточки по каждому найденному разбору (одним сообщением, без Markdown)."""
    def flag(country: str) -> str:
        c = (country or "").upper()
        return "🇨🇳" if c == "CN" else "🇰🇷" if c == "KR" else "🏳️"
    max_len = max(len(str(o.get("order_id",""))) for o in orders) if orders else 0
    lines = ["🔎 Найденные заказы:"]
    for o in orders:
        oid = str(o.get("order_id","")).strip()
        status = o.get("status","—")
        origin = o.get("origin") or o.get("country") or "—"
        updated_at = (o.get("updated_at","") or "").replace("T"," ")[11:16]
        part = sheets.get_participants(oid)
        unpaid = sum(1 for p in part if not p.get("paid"))
        client = o.get("client_name") or "—"
        lines.append(f"{oid.ljust(max_len)} · {status} · {flag(origin)} {origin} · {updated_at or '--:--'} · клиенты: {client} · долги: {unpaid}")
await reply_animated(
    update, context,
    "\n".join(lines) if lines else "Нет карточек."
)

async def _open_order_card(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Переиспользуем карточку заказа + участников."""
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        return await reply_animated(update, context, "🙈 Заказ не найден.")
    # — заголовок
    client_name = order.get("client_name", "—")
    status = order.get("status", "—")
    note = order.get("note", "—")
    country = order.get("country", order.get("origin", "—"))
    origin = order.get("origin")
    updated_at = order.get("updated_at")

    head = [
        f"*order_id:* `{order_id}`",
        f"*client_name:* {client_name}",
        f"*status:* {status}",
        f"*note:* {note}",
        f"*country:* {country}",
    ]
    if origin and origin != country:
        head.append(f"*origin:* {origin}")
    if updated_at:
        head.append(f"*updated_at:* {updated_at}")

    await reply_animated(update, context, "\n".join(head), reply_markup=order_card_kb(order_id))

    # — участники
    participants = sheets.get_participants(order_id)
    page = 0; per_page = 8
    part_text = build_participants_text(order_id, participants, page, per_page)
    kb = build_participants_kb(order_id, participants, page, per_page)
    await reply_animated(update, context, part_text, reply_markup=kb)

# ---------------------- Текст кнопок (новые + обратная совместимость) ----------------------

# Клиентские
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

# Админские
BTN_ADMIN_ADD_NEW     = "➕ Добавить разбор"
BTN_ADMIN_TRACK_NEW   = "🔎 Поиск"  # alias for «Отследить разбор»
BTN_ADMIN_SEND_NEW    = "📣 Админ: Рассылка"
BTN_ADMIN_ADDRS_NEW   = "👤 Клиенты"  # was «Адреса»
BTN_ADMIN_REPORTS_NEW = "📊 Отчёты"
BTN_ADMIN_MASS_NEW    = "🧰 Массовая смена статусов"
BTN_ADMIN_EXIT_NEW    = "🚪 Выйти из админ-панели"

BTN_BACK_TO_ADMIN_NEW = "⬅️ Назад, в админ-панель"

ADMIN_MENU_ALIASES = {
    "admin_add": {BTN_ADMIN_ADD_NEW, "добавить разбор"},
    "admin_track": {BTN_ADMIN_TRACK_NEW, "отследить разбор", "поиск"},
    "admin_send": {BTN_ADMIN_SEND_NEW, "админ: рассылка"},
    "admin_addrs": {BTN_ADMIN_ADDRS_NEW, "админ: адреса", "клиенты"},
    "admin_reports": {BTN_ADMIN_REPORTS_NEW, "отчёты"},
    "admin_mass": {BTN_ADMIN_MASS_NEW, "массовая смена статусов"},
    "admin_exit": {BTN_ADMIN_EXIT_NEW, "выйти из админ-панели"},
    "back_admin": {BTN_BACK_TO_ADMIN_NEW, "назад, в админ-панель"},
}

# Подменю «Рассылка»
BTN_BC_ALL_NEW  = "📨 Уведомления всем должникам"
BTN_BC_ONE_NEW  = "📩 Уведомления по ID разбора"

BROADCAST_ALIASES = {
    "bc_all": {BTN_BC_ALL_NEW, "уведомления всем должникам"},
    "bc_one": {BTN_BC_ONE_NEW, "уведомления по id разбора"},
}

# Подменю «Клиенты»
BTN_ADDRS_EXPORT_NEW = "📤 Выгрузить адреса клиентов"
BTN_ADDRS_EDIT_NEW   = "✏️ Изменить адрес по username"
BTN_CLIENTS_LIST_NEW = "🔎 Список/поиск клиентов"

ADMIN_ADDR_ALIASES = {
    "export_addrs": {BTN_ADDRS_EXPORT_NEW, "выгрузить адреса", "выгрузить адреса клиентов"},
    "edit_addr":    {BTN_ADDRS_EDIT_NEW, "изменить адрес по username"},
    "list_clients": {BTN_CLIENTS_LIST_NEW, "список/поиск клиентов"},
}

# Подменю «Отчёты»
BTN_REPORT_EXPORT_BY_NOTE_NEW = "🧾 Выгрузить разборы админа"
BTN_REPORT_UNPAID_NEW         = "🧮 Отчёт по должникам"
BTN_REPORT_LAST_5_NEW         = "🕒 Последние разборы"

REPORT_ALIASES = {
    "report_by_note": {BTN_REPORT_EXPORT_BY_NOTE_NEW, "выгрузить разборы админа"},
    "report_unpaid": {BTN_REPORT_UNPAID_NEW, "отчёт по должникам"},
    "report_last5": {BTN_REPORT_LAST_5_NEW, "последние разборы"},
}

def _is(text: str, group: set[str]) -> bool:
    return text.strip().lower() in {x.lower() for x in group}

# ---------------------- Клавиатуры ----------------------

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK_NEW)],
        [KeyboardButton(BTN_ADDRS_NEW), KeyboardButton(BTN_SUBS_NEW)],
        [KeyboardButton(BTN_PROFILE_NEW)],
        [KeyboardButton(BTN_CANCEL_NEW)],
    ],
    resize_keyboard=True,
)

# Админ-меню
ADMIN_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ADMIN_ADD_NEW),  KeyboardButton(BTN_ADMIN_TRACK_NEW)],
        [KeyboardButton(BTN_ADMIN_SEND_NEW), KeyboardButton(BTN_ADMIN_ADDRS_NEW)],
        [KeyboardButton(BTN_ADMIN_REPORTS_NEW), KeyboardButton(BTN_ADMIN_MASS_NEW)],
        [KeyboardButton(BTN_ADMIN_EXIT_NEW)],
    ],
    resize_keyboard=True,
)

BROADCAST_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_BC_ALL_NEW)],
        [KeyboardButton(BTN_BC_ONE_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

ADMIN_ADDR_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ADDRS_EXPORT_NEW)],
        [KeyboardButton(BTN_CLIENTS_LIST_NEW)],
        [KeyboardButton(BTN_ADDRS_EDIT_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

REPORTS_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_REPORT_EXPORT_BY_NOTE_NEW)],
        [KeyboardButton(BTN_REPORT_UNPAID_NEW)],
        [KeyboardButton(BTN_REPORT_LAST_5_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

def status_keyboard(cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"adm:pick_status_id:{i}"))
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

# Универсальная клавиатура выбора статуса с произвольным префиксом (для массового режима)
def status_keyboard_with_prefix(prefix: str, cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"{prefix}:{i}"))
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

# ------- participants UI (список с переключателями) -------

def _slice_page(items: List, page: int, per_page: int) -> Tuple[List, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start:start + per_page], total_pages

def build_participants_text(order_id: str, participants: List[dict], page: int, per_page: int) -> str:
    slice_, total_pages = _slice_page(participants, page, per_page)
    lines = [f"*Разбор* `{order_id}` — участники ({page+1}/{total_pages}):"]
    if not slice_:
        lines.append("_Список участников пуст._")
    for p in slice_:
        mark = "✅" if p.get("paid") else "❌"
        lines.append(f"{mark} @{p.get('username')}")
    return "\n".join(lines)

def build_participants_kb(order_id: str, participants: List[dict], page: int, per_page: int) -> InlineKeyboardMarkup:
    slice_, total_pages = _slice_page(participants, page, per_page)
    rows = []
    for p in slice_:
        mark = "✅" if p.get("paid") else "❌"
        rows.append([InlineKeyboardButton(f"{mark} @{p.get('username')}", callback_data=f"pp:toggle:{order_id}:{p.get('username')}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Назад", callback_data=f"pp:page:{order_id}:{page-1}"))
    nav.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"pp:refresh:{order_id}:{page}"))
    if (page + 1) * per_page < len(participants):
        nav.append(InlineKeyboardButton("Вперёд »", callback_data=f"pp:page:{order_id}:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

def order_card_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить статус", callback_data=f"adm:status_menu:{order_id}")],
        ]
    )

# ---- Подсказка для текущего шага админа ----
def _admin_mode_prompt(mode: str):
    if mode == "add_order_id":
        return "Введи order_id (например: CN-12345):", None
    if mode == "add_order_client":
        return "Имя клиента (можно несколько @username):", None
    if mode == "add_order_country":
        return "Страна/склад: введи 'CN' (Китай) или 'KR' (Корея):", None
    if mode == "add_order_status":
        return "Выбери стартовый статус кнопкой ниже или напиши точный:", status_keyboard(2)
    if mode == "add_order_note":
        return "Примечание (или '-' если нет):", None
    if mode == "adm_remind_unpaid_order":
        return "Введи order_id/список для рассылки неплательщикам:", None
    if mode == "adm_export_addrs":
        return "Пришли список @username (через пробел/запятую/новые строки):", None
    if mode == "adm_edit_addr_username":
        return "Пришли @username пользователя, чей адрес нужно изменить:", None
    if mode == "adm_edit_addr_fullname":
        return "ФИО (новое значение):", None
    if mode == "adm_edit_addr_phone":
        return "Телефон:", None
    if mode == "adm_edit_addr_city":
        return "Город:", None
    if mode == "adm_edit_addr_address":
        return "Адрес:", None
    if mode == "adm_edit_addr_postcode":
        return "Почтовый индекс:", None
    if mode == "adm_export_orders_by_note":
        return "Пришли метку/слово из note (по ней выгружу разборы):", None
    if mode == "mass_pick_status":
        return "Выбери новый статус для нескольких заказов:", status_keyboard_with_prefix("mass:pick_status_id")
    if mode == "mass_update_status_ids":
        return ("Пришли список order_id (через пробел/запятые/новые строки), "
                "например: CN-1001 CN-1002, KR-2003"), None
    return "Вы в админ-панели. Выберите действие:", ADMIN_MENU_KB

def _err_reason(e: Exception) -> str:
    s = str(e).lower()
    if "forbidden" in s or "blocked" in s:
        return "бот заблокирован"
    if "chat not found" in s or "not found" in s:
        return "нет chat_id"
    if "bad request" in s:
        return "bad request"
    if "retry after" in s or "flood" in s:
        return "rate limit"
    if "timeout" in s:
        return "timeout"
    return "ошибка"

# ---------------------- Команды ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hello = (
        "✨ Привет! Я *SEABLUU* Helper — помогу отследить разборы, адреса и подписки.\\n\\n"
        "• 🔍 Отследить разбор — статус по `order_id` (например, `CN-12345`).\\n"
        "• 🔔 Подписки — уведомлю, когда статус заказа изменится.\\n"
        "• 🏠 Мои адреса — сохраню/обновлю адрес для доставки.\\n"
        "• 👤 Профиль — ваши данные и связанные разборы.\\n\\n"
        "Если что-то пошло не так — нажми «Отмена» или используй /help."
    )
    await reply_markdown_animated(update, context, hello, reply_markup=MAIN_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_animated(
        update, context,
        "📘 Помощь:\\n"
        "• 🔍 Отследить разбор — статус по номеру\\n"
        "• 🏠 Мои адреса — добавить/изменить адрес\\n"
        "• 🔔 Мои подписки — список подписок\\n"
        "• 👤 Профиль — общая информация\\n"
        "• /admin — админ-панель (для админов)"
    )

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    for k in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id"):
        context.user_data.pop(k, None)
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)

# ---------------------- Пользовательские сценарии ----------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()


    # Если ждём ввод для поиска и админ нажал "Назад" — выходим в админ-панель
    if context.user_data.get(FIND_EXPECTING_QUERY_FLAG) and _is(text, ADMIN_MENU_ALIASES["back_admin"]):
        context.user_data.pop(FIND_EXPECTING_QUERY_FLAG, None)
        await admin_menu(update, context)
        return

    # ==== Ответ на запрос после /find (мультипоиск) ====
    if context.user_data.get(FIND_EXPECTING_QUERY_FLAG):
        context.user_data.pop(FIND_EXPECTING_QUERY_FLAG, None)
        loader = await show_loader(update, context, "⏳ Ищу…")
        try:
            # распарсим токены
            tokens = [t for t in re.split(r"[,\\s]+", raw) if t.strip()]
            if not tokens:
                return await reply_animated(update, context, "Пусто. Пришлите order_id / @username / телефон.")
            orders: List[Dict] = []
            seen = set()

            # 1) order_id
            for t in tokens:
                oid = extract_order_id(t)
                if oid and oid not in seen:
                    od = sheets.get_order(oid)
                    if od:
                        orders.append(od); seen.add(oid)

            # 2) username
            for t in tokens:
                if t.startswith("@"):
                    for od in sheets.get_orders_by_username(t):
                        oid = str(od.get("order_id","")).strip()
                        if oid and oid not in seen:
                            orders.append(od); seen.add(oid)

            # 3) phone
            for t in tokens:
                if len(re.sub(r"\\D+","",t)) >= 6 and not t.startswith("@") and not extract_order_id(t):
                    for od in sheets.get_orders_by_phone(t):
                        oid = str(od.get("order_id","")).strip()
                        if oid and oid not in seen:
                            orders.append(od); seen.add(oid)

            if not orders:
                return await reply_animated(update, context, "Ничего не нашёл по запросу.")

            context.user_data[FIND_RESULTS_KEY] = [ {"order_id": o.get("order_id","")} for o in orders ]
            context.user_data[FIND_PAGE_KEY] = 0

            # карточки
            await _render_found_cards(update, context, orders)
            # список кнопок + нижняя «массовая смена»
            kb = _build_find_results_kb(orders, page=0)
            await reply_markdown_animated(update, context, f"Найдено заказов: *{len(orders)}*. Выберите:", reply_markup=kb)
        finally:
            await safe_delete_message(context, loader)
        return

    # ===== ADMIN FLOW =====
    if _is_admin(update.effective_user.id):

        if _is(text, ADMIN_MENU_ALIASES["admin_exit"]):
            context.user_data.clear()
            await reply_animated(update, context, "🚪 Готово, вышли из админ-панели.", reply_markup=MAIN_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_add"]):
            context.user_data["adm_mode"] = "add_order_id"
            context.user_data["adm_buf"] = {}
            await reply_markdown_animated(update, context, "➕ Введи *order_id* (например: `CN-12345`):")
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_reports"]):
            await reply_animated(update, context, "📊 Раздел «Отчёты»", reply_markup=REPORTS_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_send"]):
            await reply_animated(update, context, "📣 Раздел «Рассылка»", reply_markup=BROADCAST_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_addrs"]):
            await reply_animated(update, context, "👤 Раздел «Клиенты»", reply_markup=ADMIN_ADDR_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_mass"]):
            context.user_data["adm_mode"] = "mass_pick_status"
            await reply_animated(update, context, "Выбери новый статус для нескольких заказов:", reply_markup=status_keyboard_with_prefix("mass:pick_status_id"))
            return

        if _is(text, ADMIN_MENU_ALIASES["back_admin"]):
            await admin_menu(update, context)
            return

        # --- Рассылка
        if _is(text, BROADCAST_ALIASES["bc_all"]):
            await broadcast_all_unpaid_text(update, context)
            return

        if _is(text, BROADCAST_ALIASES["bc_one"]):
            context.user_data["adm_mode"] = "adm_remind_unpaid_order"
            await reply_markdown_animated(update, context, "✉️ Введи *order_id* или *список order_id* для рассылки неплательщикам:")
            return

        # --- Клиенты (подменю)
        if _is(text, ADMIN_ADDR_ALIASES["export_addrs"]):
            context.user_data["adm_mode"] = "adm_export_addrs"
            await reply_animated(update, context, "Пришли список @username (через пробел/запятую/новые строки):")
            return

        if _is(text, ADMIN_ADDR_ALIASES["edit_addr"]):
            context.user_data["adm_mode"] = "adm_edit_addr_username"
            await reply_animated(update, context, "Пришли @username пользователя, чей адрес нужно изменить:")
            return

        if _is(text, ADMIN_ADDR_ALIASES["list_clients"]):
            context.user_data["clients_query"] = None
            context.user_data["clients_page"] = 0
            await show_clients_page(update, context)
            return

        # --- Отчёты (подменю)
        if _is(text, REPORT_ALIASES["report_by_note"]):
            context.user_data["adm_mode"] = "adm_export_orders_by_note"
            await reply_markdown_animated(update, context, "🧾 Пришли метку/слово из *note*, по которому помечены твои разборы:")
            return

        if _is(text, REPORT_ALIASES["report_unpaid"]):
            await report_unpaid(update, context)
            return

        if _is(text, REPORT_ALIASES["report_last5"]):
            await show_last_orders(update, context, limit=5)
            return

        # --- Поиск (кнопка «Поиск»/«Отследить разбор»)
        if _is(text, ADMIN_MENU_ALIASES["admin_track"]) and (context.user_data.get("adm_mode") is None):
            return await admin_find_start(update, context)

        
        # --- Поиск клиентов: ввод запроса ---
        if context.user_data.get("adm_mode") == "clients_search_wait":
            q = raw.strip()
            if q == "-" or q == "—":
                context.user_data["clients_query"] = None
            else:
                context.user_data["clients_query"] = q
            context.user_data["clients_page"] = 0
            context.user_data.pop("adm_mode", None)
            await show_clients_page(update, context)
            return

# --- Мастера/вводы ---
        a_mode = context.user_data.get("adm_mode")

        # Добавление заказа
        if a_mode == "add_order_id":
            norm = extract_order_id(raw) or raw
            prefix = (norm.split("-",1)[0] if "-" in norm else "").upper()
            if prefix not in ("CN","KR"):
                await reply_animated(update, context, "Неверный order_id. Пример: CN-12345")
                return
            context.user_data["adm_buf"] = {"order_id": norm, "country": prefix}
            context.user_data["adm_mode"] = "add_order_client"
            await reply_animated(update, context, "Имя клиента (можно несколько @username):")
            return

        if a_mode == "add_order_client":
            context.user_data["adm_buf"]["client_name"] = raw
            context.user_data["adm_mode"] = "add_order_status"
            await reply_animated(update, context, "Выбери стартовый статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
            return
            context.user_data["adm_buf"]["country"] = country
            context.user_data["adm_mode"] = "add_order_status"
            await reply_animated(update, context, "Выбери стартовый статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
            return

        if a_mode == "add_order_status":
            if not is_valid_status(raw, STATUSES):
                await reply_animated(update, context, "Выбери статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
                return
            context.user_data["adm_buf"]["status"] = raw.strip()
            context.user_data["adm_mode"] = "add_order_note"
            await reply_animated(update, context, "Примечание (или '-' если нет):")
            return

        if a_mode == "add_order_note":
            buf = context.user_data.get("adm_buf", {})
            buf["note"] = raw if raw != "-" else ""
            try:
                sheets.add_order({
                    "order_id": buf["order_id"],
                    "client_name": buf.get("client_name", ""),
                    "country": buf.get("country", ""),
                    "status": buf.get("status", "выкуплен"),
                    "note": buf.get("note", ""),
                })
                usernames = [m.group(1) for m in USERNAME_RE.finditer(buf.get("client_name", ""))]
                if usernames:
                    sheets.ensure_participants(buf["order_id"], usernames)
                    # подписка и уведомление о создании
                    ids = sheets.get_user_ids_by_usernames(usernames)
                    for uid in ids:
                        try:
                            sheets.subscribe(uid, buf["order_id"])
                        except Exception:
                            pass
                        try:
                            await context.bot.send_message(
                                chat_id=uid,
                                text=f"🆕 Создан новый разбор *{buf['order_id']}*. Текущий статус: *{buf.get('status','')}*",
                                parse_mode="Markdown",
                            )
                        except Exception as e:
                            logger.warning(f"notify new order fail to {uid}: {e}")
                await reply_markdown_animated(update, context, f"✅ Заказ *{buf['order_id']}* добавлен")
            except Exception as e:
                await reply_animated(update, context, f"Ошибка: {e}")
            finally:
                for k in ("adm_mode", "adm_buf"):
                    context.user_data.pop(k, None)
            return

        # Ручная рассылка по нескольким order_id
        if a_mode == "adm_remind_unpaid_order":
            tokens = [t for t in re.split(r"[,\\s]+", raw.strip()) if t]
            ids = []
            seen = set()
            for t in tokens:
                oid = extract_order_id(t) or None
                if oid and oid not in seen:
                    seen.add(oid); ids.append(oid)
            if not ids:
                await reply_animated(update, context, "🙈 Не понял. Пришли один или несколько *order_id*.")
                return
            loader = await show_loader(update, context, "⏳ Рассылаю…")
            try:
                reports = []
                for oid in ids:
                    ok, rep = await remind_unpaid_for_order(context.application, oid)
                    reports.append(rep)
                await reply_animated(update, context, "\n\n".join(reports))
            finally:
                await safe_delete_message(context, loader)
            context.user_data.pop("adm_mode", None)
            return


        if a_mode == "mass_update_status_ids":
            tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
            ids = []
            seen = set()
            for t in tokens:
                oid = extract_order_id(t)
                if oid and oid not in seen:
                    seen.add(oid); ids.append(oid)
            if not ids:
                await reply_animated(update, context, "⚠️ Не понял. Пришли список order_id (через пробел/запятые/новые строки), например: CN-1001 CN-1002, KR-2003")
                return
            new_status = context.user_data.get("mass_status")
            if not new_status:
                await reply_animated(update, context, "Сначала выбери новый статус.")
                context.user_data.pop("adm_mode", None)
                return
            loader = await show_loader(update, context, "⏳ Обновляю статусы…")
            try:
                ok = 0; fail = []
                for oid in ids:
                    try:
                        if sheets.update_order_status(oid, new_status):
                            ok += 1
                            try: await notify_subscribers(context.application, oid, new_status)
                            except Exception: pass
                        else:
                            fail.append(oid)
                    except Exception:
                        fail.append(oid)
                lines = ["✏️ Массовая смена статусов — итог",
                         f"Всего: {len(ids)}",
                         f"✅ Обновлено: {ok}",
                         f"❌ Ошибки: {len(fail)}"]
                if fail:
                    lines.append("Не удалось: " + ", ".join(fail))
                await reply_animated(update, context, "\n".join(lines))
            finally:
                await safe_delete_message(context, loader)
            context.user_data.pop("adm_mode", None)
            context.user_data.pop("mass_status", None)
            return
        # Выгрузить адреса клиентов (по списку username) — ТЕКСТОМ, не файлом
        if a_mode == "adm_export_addrs":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await reply_animated(update, context, "Пришли список @username.")
                return
            rows = sheets.get_clients_by_usernames(usernames)
            if not rows:
                await reply_animated(update, context, "Клиенты не найдены.")
            else:
                blocks = []
                for r in rows:
                    blocks.append(
                        f"@{r.get('username','').lstrip('@')}\\n"
                        f"ФИО: {r.get('full_name','')}\\n"
                        f"Телефон: {r.get('phone','')}\\n"
                        f"Город: {r.get('city','')}\\n"
                        f"Адрес: {r.get('address','')}\\n"
                        f"Индекс: {r.get('postcode','')}\\n"
                        f"created_at: {r.get('created_at','')}\\n"
                        "—"
                    )
                await reply_animated(update, context, "\\n".join(blocks))
            context.user_data.pop("adm_mode", None)
            return

        # Изменить адрес по username — шаги мастера (оставляем как было)
        if a_mode == "adm_edit_addr_username":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await reply_animated(update, context, "Пришли @username.")
                return
            uname = usernames[0].lower()
            ids = sheets.get_user_ids_by_usernames([uname])
            if not ids:
                await reply_animated(update, context, "Пользователь не найден по username (нет записи в адресах/клиентах).")
                context.user_data.pop("adm_mode", None)
                return
            context.user_data["adm_mode"] = "adm_edit_addr_fullname"
            context.user_data["adm_buf"] = {"edit_user_id": ids[0], "edit_username": uname}
            await reply_animated(update, context, "ФИО (новое значение):")
            return

        if a_mode == "adm_edit_addr_fullname":
            context.user_data.setdefault("adm_buf", {})["full_name"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_phone"
            await reply_animated(update, context, "Телефон:")
            return

        if a_mode == "adm_edit_addr_phone":
            context.user_data["adm_buf"]["phone"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_city"
            await reply_animated(update, context, "Город:")
            return

        if a_mode == "adm_edit_addr_city":
            context.user_data["adm_buf"]["city"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_address"
            await reply_animated(update, context, "Адрес:")
            return

        if a_mode == "adm_edit_addr_address":
            context.user_data["adm_buf"]["address"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_postcode"
            await reply_animated(update, context, "Почтовый индекс:")
            return

        if a_mode == "adm_edit_addr_postcode":
            buf = context.user_data.get("adm_buf", {})
            try:
                sheets.upsert_address(
                    user_id=buf["edit_user_id"],
                    username=buf.get("edit_username",""),
                    full_name=buf.get("full_name",""),
                    phone=buf.get("phone",""),
                    city=buf.get("city",""),
                    address=buf.get("address",""),
                    postcode=raw,
                )
                await reply_animated(update, context, "✅ Данные клиента обновлены")
            except Exception as e:
                await reply_animated(update, context, f"Ошибка: {e}")
            finally:
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
            return

        # Выгрузить разборы по note
        if a_mode == "adm_export_orders_by_note":
            marker = raw.strip()
            if not marker:
                await reply_animated(update, context, "Пришли метку/слово для поиска в note.")
                return
            orders = sheets.get_orders_by_note(marker)
            if not orders:
                await reply_animated(update, context, "Ничего не найдено.")
            else:
                lines = []
                for o in orders:
                    lines.append(
                        f"*order_id:* `{o.get('order_id','')}`\\n"
                        f"*client_name:* {o.get('client_name','')}`\\n"
                        f"*status:* {o.get('status','')}`\\n"
                        f"*country:* {o.get('country','')}`\\n"
                        f"*updated_at:* {o.get('updated_at','')}`\\n"
                        "—"
                    )
                await reply_animated(update, context, "\n".join(lines))
            context.user_data.pop("adm_mode", None)
            return

    # ===== USER FLOW =====
    if _is(text, CLIENT_ALIASES["cancel"]):
        context.user_data["mode"] = None
        await reply_animated(update, context, "Отменили действие. Что дальше? 🙂", reply_markup=MAIN_KB)
        return

    if _is(text, CLIENT_ALIASES["track"]):
        context.user_data["mode"] = "track"
        await reply_animated(update, context, "🔎 Отправьте номер заказа (например: CN-12345):")
        return

    if _is(text, CLIENT_ALIASES["addrs"]):
        context.user_data["mode"] = None
        await show_addresses(update, context)
        return

    if _is(text, CLIENT_ALIASES["subs"]):
        context.user_data["mode"] = None
        await show_subscriptions(update, context)
        return

    if _is(text, CLIENT_ALIASES["profile"]):
        await show_profile(update, context)
        return

    mode = context.user_data.get("mode")
    if mode == "track":
        await query_status(update, context, raw)
        return

    # ====== Мастер адреса ======
    if mode == "add_address_fullname":
        context.user_data["full_name"] = raw
        await reply_animated(update, context, "📞 Телефон (пример: 87001234567):")
        context.user_data["mode"] = "add_address_phone"
        return

    if mode == "add_address_phone":
        normalized = raw.strip().replace(" ", "").replace("-", "")
        if normalized.startswith("+7"): normalized = "8" + normalized[2:]
        elif normalized.startswith("7"): normalized = "8" + normalized[1:]
        if not (normalized.isdigit() and len(normalized) == 11 and normalized.startswith("8")):
            await reply_animated(update, context, "Нужно 11 цифр и обязательно с 8. Пример: 87001234567\\nВведи номер ещё раз или нажми «Отмена».")
            return
        context.user_data["phone"] = normalized
        await reply_animated(update, context, "🏙 Город (пример: Астана):")
        context.user_data["mode"] = "add_address_city"
        return

    if mode == "add_address_city":
        context.user_data["city"] = raw
        await reply_animated(update, context, "🏠 Адрес (свободный формат):")
        context.user_data["mode"] = "add_address_address"
        return

    if mode == "add_address_address":
        context.user_data["address"] = raw
        await reply_animated(update, context, "📮 Почтовый индекс (пример: 010000):")
        context.user_data["mode"] = "add_address_postcode"
        return

    if mode == "add_address_postcode":
        if not (raw.isdigit() and 5 <= len(raw) <= 6):
            await reply_animated(update, context, "Индекс выглядит странно. Пример: 010000\\nВведи индекс ещё раз или нажми «Отмена».")
            return
        context.user_data["postcode"] = raw
        await save_address(update, context)
        return


    # Если мы в режиме поиска, но прислали что-то странное (не "-" и не back), напомним формат
    if context.user_data.get(FIND_EXPECTING_QUERY_FLAG):
        await reply_markdown_animated(update, context, "🔎 *Поиск заказов*\nПришлите `order_id`, `@username` или телефон.\nЧтобы выйти — нажмите «⬅️ Назад, в админ-панель».", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK_TO_ADMIN_NEW)]], resize_keyboard=True))
        return

    # Ничего не подошло — ветки админ/клиент
    if _is_admin(update.effective_user.id):
        a_mode = context.user_data.get("adm_mode")
        if a_mode:
            msg, kb = _admin_mode_prompt(a_mode)
            await reply_animated(update, context, f"⚠️ Не понял. {msg}", reply_markup=kb or ADMIN_MENU_KB)
            return
        await reply_animated(update, context, "Вы в админ-панели. Выберите действие:", reply_markup=ADMIN_MENU_KB)
        return

    await reply_animated(
        update, context,
        "Хмм, не понял. Выберите кнопку ниже или введите номер заказа. Если что — «Отмена».",
        reply_markup=MAIN_KB,
    )

# ---------------------- Клиент: статус/подписки/адреса/профиль ----------------------

async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    await _typing(context, update.effective_chat.id, 0.5)
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        await reply_animated(update, context, "🙈 Такой заказ не найден. Проверьте номер или повторите позже.")
        return
    status = order.get("status") or "статус не указан"
    origin = order.get("origin") or ""
    txt = f"📦 Заказ *{order_id}*\\nСтатус: *{status}*"
    if origin:
        txt += f"\\nСтрана/источник: {origin}"

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
        lines.append(f"• {a['full_name']} — {a['phone']}\\n{a['city']}, {a['address']}, {a['postcode']}")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
            [InlineKeyboardButton("🗑 Удалить адрес", callback_data="addr:del")],
        ]
    )
    await reply_animated(update, context, "📍 Ваш адрес доставки:\\n" + "\\n\\n".join(lines), reply_markup=kb)

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
    # автоподписка на свои разборы
    try:
        if u.username:
            for oid in sheets.find_orders_for_username(u.username):
                try: sheets.subscribe(u.id, oid)
                except Exception: pass
    except Exception as e:
        logger.warning(f"auto-subscribe failed: {e}")

    context.user_data["mode"] = None
    msg = (
        "✅ Адрес сохранён!\\n\\n"
        f"👤 ФИО: {context.user_data.get('full_name','')}\\n"
        f"📞 Телефон: {context.user_data.get('phone','')}\\n"
        f"🏙 Город: {context.user_data.get('city','')}\\n"
        f"🏠 Адрес: {context.user_data.get('address','')}\\n"
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
    await reply_animated(update, context, "🔔 Ваши подписки:\\n" + "\\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb_rows))


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user

    # базовые данные
    addresses = sheets.list_addresses(u.id)
    addr = addresses[0] if addresses else {}

    # связанные разборы
    orders = sheets.find_orders_for_username(u.username or "") if (u.username) else []
    order_lines = []
    for oid in orders[:10]:
        o = sheets.get_order(oid) or {}
        order_lines.append(f"• {oid} — {o.get('status', '—')}")

    # если больше 10, добавим хвост
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

# ---------- Уведомления подписчикам ----------

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
                text=f"🔄 Обновление по заказу *{order_id}*\\nНовый статус: *{new_status}*",
                parse_mode="Markdown",
            )
            try: sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception: pass
        except Exception as e:
            logger.warning(f"notify_subscribers fail to {uid}: {e}")

# ---------- Напоминания об оплате ----------


async def remind_unpaid_for_order(application, order_id: str) -> tuple[bool, str]:
    # даже если заказа нет в orders — продолжаем по participants
    order = sheets.get_order(order_id)
    usernames = sheets.get_unpaid_usernames(order_id)  # список username без @
    if order is None and not usernames:
        return False, "🙈 Заказ не найден."
    if not usernames:
        return False, f"🎉 По заказу *{order_id}* должников нет — красота!"

    lines = [f"📩 Уведомления по ID разбора — {order_id}"]
    ok_cnt, fail_cnt = 0, 0

    for uname in usernames:
        ids = sheets.get_user_ids_by_usernames([uname]) or []
        if not ids:
            fail_cnt += 1
            lines.append(f"• ❌ @{uname} — нет chat_id")
            continue

        uid = ids[0]
        try:
            try:
                sheets.subscribe(uid, order_id)
            except Exception:
                pass

            await application.bot.send_message(
                chat_id=uid,
                text=(
                    f"💳 Напоминание по разбору *{order_id}*
"
                    f"Статус: *Доставка не оплачена*

"
                    f"Пожалуйста, оплатите доставку. Если уже оплатили — можно игнорировать."
                ),
                parse_mode="Markdown",
            )
            ok_cnt += 1
            lines.append(f"• ✅ @{uname}")
        except Exception as e:
            fail_cnt += 1
            lines.append(f"• ❌ @{uname} — {_err_reason(e)}")

    lines.append("")
    lines.append(f"_Итого:_ ✅ {ok_cnt}  ❌ {fail_cnt}")
    return True, "
".join(lines)


UNPAID_PAGE_KEY = "unpaid_page"
UNPAID_ORDERIDS_KEY = "unpaid_ids"

def _render_unpaid_page(grouped: Dict[str, list], page: int, per_page: int = 15) -> tuple[str, InlineKeyboardMarkup]:
    # grouped: {order_id: [usernames...]}
    order_ids = list(grouped.keys())
    total_pages = max(1, (len(order_ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages-1))
    start = page * per_page
    chunk = order_ids[start:start+per_page]

    lines = [f"📋 Отчёт по должникам ({page+1}/{total_pages}):"]
    for oid in chunk:
        users = grouped.get(oid, [])
        ulist = ", ".join([f"@{u}" for u in users]) if users else "—"
        lines.append(f"• {oid}: {ulist}")
    # nav
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀︎", callback_data=f"unpaid:page:{page-1}"))
    if start + per_page < len(order_ids):
        nav.append(InlineKeyboardButton("▶︎", callback_data=f"unpaid:page:{page+1}"))
    kb = InlineKeyboardMarkup([nav]) if nav else None
    return "\n".join(lines), kb


async def report_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    if not grouped:
        await reply_animated(update, context, "🎉 Должников не найдено — красота!")
        return
    # сохраняем в сессию
    context.user_data[UNPAID_ORDERIDS_KEY] = grouped
    context.user_data[UNPAID_PAGE_KEY] = 0
    text_body, kb = _render_unpaid_page(grouped, 0, per_page=15)
    await reply_animated(update, context, text_body, reply_markup=kb)

async def broadcast_all_unpaid_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    if not grouped:
        await reply_animated(update, context, "🎉 Должников по всем разборам нет — супер!")
        return
    loader = await show_loader(update, context, "⏳ Отправляю уведомления…")
    try:
        total_ok = total_fail = 0
        per_order_lines = []
        for order_id, _users in grouped.items():
            ok, rep = await remind_unpaid_for_order(context.application, order_id)
            per_order_lines.append(rep)
            # грубая агрегация по тексту отчёта:
            total_ok += rep.count("• ✅")
            total_fail += rep.count("• ❌")
        per_order_lines.append("")
        per_order_lines.append(f"_Итого по всем:_ ✅ {total_ok}  ❌ {total_fail}")
        await reply_animated(update, context, "\n\n".join(per_order_lines))
    finally:
        await safe_delete_message(context, loader)

# ---------------------- Отчёты ----------------------


async def show_last_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int = 5):
    loader = await show_loader(update, context, "⏳ Собираю последние разборы…")
    try:
        items = sheets.list_recent_orders(limit=limit)
        if not items:
            await reply_animated(update, context, "Пусто.")
            return

        def flag(country: str) -> str:
            c = (country or "").upper()
            return "🇨🇳" if c == "CN" else "🇰🇷" if c == "KR" else "🏳️"

        # Заголовок с датой первой записи
        first_dt = (items[0].get("updated_at","") or "").replace("T", " ")
        first_d = first_dt[:10] if first_dt else ""
        head = "🕒 Последние разборы" + (f" — {first_d}" if first_d else "") + ":"

        max_len = max(len(str(o.get("order_id",""))) for o in items)
        lines = [head]
        for o in items:
            oid = str(o.get("order_id",""))
            st  = str(o.get("status","")).strip() or "—"
            country = (o.get("origin") or o.get("country") or "").upper()
            dt_iso = (o.get("updated_at","") or "")
            dt = dt_iso.replace("T", " ")
            dt_short = dt[11:16] if len(dt) >= 16 else dt
            lines.append(f"{oid.ljust(max_len)} · {st} · {flag(country)} {country or '—'} · {dt_short}")
await reply_animated(update, context, "\n".join(lines))

# ---------------------- Клиенты: список/поиск с пагинацией ----------------------


def _render_clients_page_text_kb(context: ContextTypes.DEFAULT_TYPE, query, page: int):
    page_size = 5
    items, total_count = sheets.list_clients(page=page, size=page_size, query=query)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    if not items:
        txt = "Клиенты не найдены." if query else "Пока нет клиентов."
        return txt, InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Поиск", callback_data="clients:search:ask")]])
    blocks = []
    for c in items:
        uname = f"@{str(c.get('username','')).lstrip('@')}" if c.get("username") else "—"
        orders = sheets.orders_for_username(c.get("username",""), only_active=True)
        ord_line = ", ".join([f"{oid} ({st})" for oid, st in orders]) if orders else "—"
        blocks.append(
            f"{uname}\n"
            f"ФИО: {c.get('full_name','')}\n"
            f"Телефон: {c.get('phone','')}\n"
            f"Город: {c.get('city','')}\n"
            f"Адрес: {c.get('address','')}\n"
            f"Индекс: {c.get('postcode','')}\n"
            f"Активные разборы: {ord_line}\n"
            "—"
        )
    head = f"📚 Список клиентов ({page+1}/{total_pages})" + (f" — поиск: *{query}*" if query else "")
    # nav keyboard
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀︎", callback_data=f"clients:list:{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("▶︎", callback_data=f"clients:list:{page+1}"))
    rows = [[InlineKeyboardButton("🔎 Поиск", callback_data="clients:search:ask")]]
    if nav:
        rows.append(nav)
    kb = InlineKeyboardMarkup(rows)
    return (head + "\n" + "\n".join(blocks)), kb


def _clients_nav_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀︎", callback_data=f"clients:list:{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("▶︎", callback_data=f"clients:list:{page+1}"))
    ask = [InlineKeyboardButton("🔎 Поиск", callback_data="clients:search:ask")]
    rows = []
    if ask:
        rows.append(ask)
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def show_clients_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If called from callback, edit the existing message instead of sending a new one
    loader = await show_loader(update, context, "⏳ Загружаю клиентов…")
    try:
        query = context.user_data.get("clients_query")
        page = int(context.user_data.get("clients_page") or 0)
        text_body, kb = _render_clients_page_text_kb(context, query, page)
        if update.callback_query:
            await update.callback_query.message.edit_text(text_body, reply_markup=kb)
        else:
            await reply_animated(update, context, text_body, reply_markup=kb)
    finally:
        await safe_delete_message(context, loader)

# ---------------------- Callback Router ----------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    # Подписки
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

    # Адрес
    if data == "addr:add":
        await q.message.reply_text("👤 ФИО:")
        context.user_data["mode"] = "add_address_fullname"
        return
    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await q.message.reply_text("🗑 Адрес удалён" if ok else "Адресов не было")
        return

    if data == "client:subs":
        # shortcut на \"Мои подписки\"
        await show_subscriptions(update, context)
        return

    # Участники
    if data.startswith("pp:toggle:"):
        _, _, oid, uname = data.split(":",3)
        sheets.toggle_participant_paid(oid, uname)
        participants = sheets.get_participants(oid)
        try:
            page = int(data.split(":")[-1])
        except Exception:
            page = 0
        part_text = build_participants_text(oid, participants, 0, 8)
        kb = build_participants_kb(oid, participants, 0, 8)
        await q.message.reply_text(part_text, reply_markup=kb)
        return
    if data.startswith("pp:page:") or data.startswith("pp:refresh:"):
        parts = data.split(":")
        oid = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        participants = sheets.get_participants(oid)
        part_text = build_participants_text(oid, participants, page, 8)
        kb = build_participants_kb(oid, participants, page, 8)
        await q.message.edit_text(part_text, reply_markup=kb)
        return

    # Пагинация отчёта по должникам
    if data.startswith('unpaid:page:'):
        page = int(data.split(':')[-1])
        grouped = context.user_data.get(UNPAID_ORDERIDS_KEY) or {}
        txt, kb = _render_unpaid_page(grouped, page, per_page=15)
        try:
            await q.message.edit_text(txt, reply_markup=kb)
        except Exception:
            await q.message.reply_text(txt, reply_markup=kb)
        context.user_data[UNPAID_PAGE_KEY] = page
        return

    # Меню статусов для одного заказа
    if data.startswith("adm:status_menu:"):
        order_id = data.split(":",2)[2]
        await q.message.reply_text("Выбери статус:", reply_markup=status_keyboard_with_prefix(f"adm:set_status:{order_id}"))
        return
    if data.startswith("adm:set_status:"):
        _, _, rest = data.split(":",2)
        order_id, idx = rest.rsplit(":",1)
        idx = int(idx); new_status = STATUSES[idx]
        if sheets.update_order_status(order_id, new_status):
            await q.message.reply_text(f"✅ Статус *{order_id}* обновлён: {new_status}", parse_mode="Markdown")
            try:
                await notify_subscribers(context.application, order_id, new_status)
            except Exception:
                pass
        else:
            await q.message.reply_text("🙈 Не нашёл такой заказ.")
        return

    # Массовая смена статусов — шаг выбора статуса
    if data.startswith("mass:pick_status_id:"):
        idx = int(data.split(":")[-1])
        context.user_data["adm_mode"] = "mass_update_status_ids"
        context.user_data["mass_status"] = STATUSES[idx]
        await q.message.reply_text("Пришли список order_id (через пробел/запятые/новые строки), например: CN-1001 CN-1002, KR-2003")
        return

    # Поиск: открыть заказ
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
    if data == "find:bulk:ask":
        items = context.user_data.get(FIND_RESULTS_KEY) or []
        ids = [i.get("order_id") for i in items if i.get("order_id")]
        if not ids:
            await q.message.reply_text("Сначала выполните поиск.")
            return
        context.user_data["find_last_ids"] = ids
        await q.message.reply_text(f"Выбери новый статус для *{len(ids)}* заказов:", parse_mode="Markdown", reply_markup=status_keyboard_with_prefix("findbulk:pick"))
        return
    if data.startswith("findbulk:pick:"):
        idx = int(data.split(":")[-1])
        new_status = STATUSES[idx]
        ids = context.user_data.get("find_last_ids") or []
        if not ids:
            await q.message.reply_text("Нечего обновлять.")
            return
        ok = 0; fail = []
        for oid in ids:
            try:
                updated = sheets.update_order_status(oid, new_status)
                if updated:
                    ok += 1
                    try: await notify_subscribers(context.application, oid, new_status)
                    except Exception: pass
                else:
                    fail.append(oid)
            except Exception:
                fail.append(oid)
        context.user_data.pop("find_last_ids", None)
        lines = [
            "✏️ Массовая смена статусов (из поиска) — итог",
            f"Всего: {len(ids)}",
            f"✅ Обновлено: {ok}",
            f"❌ Ошибки: {len(fail)}"
        ]
        if fail:
            lines.append("Не удалось: " + ", ".join(fail))
        await q.message.reply_text("\n".join(lines))
        return

    # Клиенты: пагинация и поиск
    if data.startswith("clients:list:"):
        page = int(data.split(":")[-1])
        context.user_data["clients_page"] = page
        await show_clients_page(update, context)
        return
    if data == "clients:search:ask":
        await q.message.reply_text("Отправь текст для поиска (username/часть ФИО/цифры телефона). Чтобы показать всех — пришли `-`.", parse_mode="Markdown")
        context.user_data["adm_mode"] = "clients_search_wait"
        return

# ---------------------- Вспомогательные сценарии ----------------------

# (Дополнительный обработчик текстов после clients:search:ask)
async def post_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Этот хэндлер вызывайте в webhook'е ПЕРЕД handle_text, если хотите ловить поисковый запрос.
    if context.user_data.get("adm_mode") == "clients_search_wait":
        q = (update.message.text or "").strip()
        if q == "-" or q == "—":
            context.user_data["clients_query"] = None
        else:
            context.user_data["clients_query"] = q
        context.user_data["clients_page"] = 0
        context.user_data.pop("adm_mode", None)
        await show_clients_page(update, context)
        return
# === Регистрация хэндлеров для webhook ===
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

def register_handlers(app: Application) -> None:
    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_menu))

    # если у тебя есть команда /find — оставь строку ниже; если нет — можно удалить
    try:
        app.add_handler(CommandHandler("find", admin_find_start))
    except NameError:
        pass

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # обычный текст — в самом конце, чтобы не перехватывать команды
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

__all__ = [
    "register_handlers",
    "start", "help_cmd", "admin_menu",
    "handle_text", "on_callback",
]

# END
