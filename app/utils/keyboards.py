from telegram import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)
from app.config import STATUSES

# Текст кнопок
BTN_TRACK_NEW = "🔍 Отследить разбор"
BTN_ADDRS_NEW = "🏠 Мои адреса"
BTN_SUBS_NEW  = "🔔 Мои подписки"
BTN_CANCEL_NEW = "❌ Отмена"

BTN_ADMIN_ADD_NEW     = "➕ Добавить разбор"
BTN_ADMIN_TRACK_NEW   = "🔎 Отследить разбор"
BTN_ADMIN_SEND_NEW    = "📣 Админ: Рассылка"
BTN_ADMIN_ADDRS_NEW   = "📇 Админ: Адреса"
BTN_ADMIN_REPORTS_NEW = "📊 Отчёты"
BTN_ADMIN_MASS_NEW    = "🧰 Массовая смена статусов"
BTN_ADMIN_EXIT_NEW    = "🚪 Выйти из админ-панели"

BTN_BACK_TO_ADMIN_NEW = "⬅️ Назад, в админ-панель"

# Подменю «Рассылка»
BTN_BC_ALL_NEW  = "📨 Уведомления всем должникам"
BTN_BC_ONE_NEW  = "📩 Уведомления по ID разбора"

# Подменю «Адреса»
BTN_ADDRS_EXPORT_NEW = "📤 Выгрузить адреса"
BTN_ADDRS_EDIT_NEW   = "✏️ Изменить адрес по username"

# Подменю «Отчёты»
BTN_REPORT_EXPORT_BY_NOTE_NEW = "🧾 Выгрузить разборы админа"
BTN_REPORT_UNPAID_NEW         = "🧮 Отчёт по должникам"

# Основные клавиатуры
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK_NEW)],
        [KeyboardButton(BTN_ADDRS_NEW), KeyboardButton(BTN_SUBS_NEW)],
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

# Клавиатуры подменю
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
        [KeyboardButton(BTN_ADDRS_EDIT_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

REPORTS_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_REPORT_EXPORT_BY_NOTE_NEW)],
        [KeyboardButton(BTN_REPORT_UNPAID_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

def status_keyboard(cols: int = 2) -> InlineKeyboardMarkup:
    """Клавиатура выбора статуса"""
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"adm:pick_status_id:{i}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def status_keyboard_with_prefix(prefix: str, cols: int = 2) -> InlineKeyboardMarkup:
    """Универсальная клавиатура статусов с префиксом"""
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"{prefix}:{i}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def order_card_kb(order_id: str) -> InlineKeyboardMarkup:
    """Клавиатура для карточки заказа"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить статус", callback_data=f"adm:status_menu:{order_id}")]
    ])

def build_participants_kb(order_id: str, participants: list, page: int, per_page: int = 8) -> InlineKeyboardMarkup:
    """Клавиатура для списка участников с пагинацией"""
    from app.utils.helpers import _slice_page
    
    slice_, total_pages = _slice_page(participants, page, per_page)
    rows = []
    
    for p in slice_:
        mark = "✅" if p.paid else "❌"
        rows.append([
            InlineKeyboardButton(
                f"{mark} @{p.username}", 
                callback_data=f"pp:toggle:{order_id}:{p.username}"
            )
        ])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Назад", callback_data=f"pp:page:{order_id}:{page-1}"))
    
    nav.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"pp:refresh:{order_id}:{page}"))
    
    if (page + 1) * per_page < len(participants):
        nav.append(InlineKeyboardButton("Вперёд »", callback_data=f"pp:page:{order_id}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    return InlineKeyboardMarkup(rows)
