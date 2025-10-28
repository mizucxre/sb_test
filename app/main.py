# -*- coding: utf-8 -*-
# app/main.py

import os
import re
import io
import asyncio
from typing import List, Dict, Any

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from app import sheets, texts

# ========== Helpers ==========
ADMIN_IDS = set()
_admin_env = os.getenv("ADMIN_IDS", "")
if _admin_env:
    for t in re.split(r"[,\s]+", _admin_env):
        t = t.strip()
        if t.isdigit():
            ADMIN_IDS.add(int(t))

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

async def _typing(update: Update, context: ContextTypes.DEFAULT_TYPE, delay: float = 0.2):
    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(delay)

async def say_md(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kw):
    await _typing(update, context)
    return await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **kw)

async def say(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kw):
    await _typing(update, context)
    return await update.effective_message.reply_text(text, **kw)

def digits_only(s: str) -> str:
    return re.sub(r"\D+", "", str(s or ""))

# ========== User menu ==========
BTN_TRACK = "🔍 Отследить разбор"
BTN_ADDR  = "🏠 Мой адрес"
BTN_SUBS  = "🔔 Мои подписки"

USER_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_TRACK)], [KeyboardButton(BTN_ADDR)], [KeyboardButton(BTN_SUBS)]],
    resize_keyboard=True
)

# ========== Admin menu ==========
BTN_ADM_SEARCH  = "🔎 Поиск"
BTN_ADM_CLIENTS = "👤 Клиенты"

ADMIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_ADM_SEARCH), KeyboardButton(BTN_ADM_CLIENTS)]],
    resize_keyboard=True
)

def clients_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Выгрузить адреса клиентов", callback_data="clients:export")],
        [InlineKeyboardButton("✏️ Изменить клиента по username", callback_data="clients:edit")],
        [InlineKeyboardButton("⬅ Назад", callback_data="clients:back")]
    ])

# ========== Start / Help / Admin ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await say_md(update, context, texts.HELLO, reply_markup=USER_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await say(update, context, texts.HELP)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    await say(update, context, "🛠 Админ-панель", reply_markup=ADMIN_KB)

# ========== Find flow ==========
FIND_AWAIT = "find:await"
FIND_RESULTS = "find:results"
FIND_PAGE = "find:page"

ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \\-–—_]*([A-Z0-9]{2,})", re.IGNORECASE)

def extract_order_id(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = ORDER_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2).upper()}"
    if "-" in s:
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and left.replace(" ", "").isalpha():
            import re as _re
            right_norm = _re.sub(r"[^A-Z0-9]+", "", right, flags=_re.I)
            if right_norm:
                return f"{left.upper()}-{right_norm.upper()}"
    return None

def guess_token_type(token: str) -> str:
    token = token.strip()
    if not token:
        return "unknown"
    if token.startswith("@"):
        return "username"
    if extract_order_id(token):
        return "order_id"
    if len(digits_only(token)) >= 6:
        return "phone"
    return "unknown"

async def admin_search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    context.user_data[FIND_AWAIT] = True
    msg = (
        "🔎 *Поиск заказов*\n"
        "Пришли одно или несколько значений (в любом порядке):\n"
        "• `order_id` (например, CN-12345)\n"
        "• `@username`\n"
        "• телефон (любой формат)\n\n"
        "Разделяй пробелами, запятыми или переносами строк."
    )
    await say_md(update, context, msg)

async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    return await admin_search_entry(update, context)

def build_results_kb(items: List[Dict[str, Any]], page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    total = len(items)
    start = page * per_page
    chunk = items[start:start+per_page]
    rows = []
    for od in chunk:
        oid = od.get("order_id")
        lab = f"📦 {oid} — {od.get('status','—')}"
        rows.append([InlineKeyboardButton(lab, callback_data=f"find:open:{oid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"find:page:{page-1}"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("▶", callback_data=f"find:page:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

async def process_find_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str):
    tokens = [t for t in re.split(r"[,\n\t ]+", raw) if t.strip()]
    if not tokens:
        return await say(update, context, "Пусто. Пришли `order_id`, `@username` или телефон.")

    orders: List[Dict[str, Any]] = []
    seen = set()

    # direct order_ids
    for t in tokens:
        oid = extract_order_id(t)
        if oid and oid not in seen:
            od = sheets.get_order(oid)
            if od:
                orders.append(od); seen.add(oid)

    # usernames
    usernames = [t for t in tokens if guess_token_type(t) == "username"]
    for u in usernames:
        for od in sheets.get_orders_by_username(u):
            oid = od.get("order_id")
            if oid and oid not in seen:
                orders.append(od); seen.add(oid)

    # phones via clients->participants
    phones = [t for t in tokens if guess_token_type(t) == "phone"]
    for p in phones:
        for od in sheets.get_orders_by_phone(p):
            oid = od.get("order_id")
            if oid and oid not in seen:
                orders.append(od); seen.add(oid)

    if not orders:
        return await say(update, context, "Ничего не нашёл по запросу.")

    context.user_data[FIND_RESULTS] = orders
    context.user_data[FIND_PAGE] = 0
    kb = build_results_kb(orders, 0)
    await say_md(update, context, f"Найдено заказов: *{len(orders)}*. Выберите:", reply_markup=kb)

async def open_order_card(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    od = sheets.get_order(order_id)
    if not od:
        return await say(update, context, "Заказ не найден.")
    # order header
    head = [
        f"*order_id:* `{order_id}`",
        f"*client_name:* {od.get('client_name','—')}",
        f"*status:* {od.get('status','—')}",
        f"*note:* {od.get('note','—')}",
        f"*country:* {od.get('country') or od.get('origin') or '—'}",
        f"*updated_at:* {od.get('updated_at','—')}",
    ]
    await say_md(update, context, "\n".join(head))
    # participants
    parts = sheets.list_participants(order_id)
    if parts:
        lines = ["*Участники:*"]
        for r in parts[:50]:
            u = r.get("username") or "—"
            paid = r.get("paid")
            paid_ico = "✅" if str(paid).strip().lower() in {"1","true","yes","да","paid","оплачен","оплачено"} else "❌"
            qty = r.get("qty") or ""
            lines.append(f"• {u} — {paid_ico} qty={qty}")
        await say_md(update, context, "\n".join(lines))

# ========== Clients admin flow ==========
EDIT_STAGE = "clients:edit_stage"
EDIT_BUF   = "clients:edit_buf"

def ask_clients_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return update.effective_message.reply_text("Раздел «Клиенты»:", reply_markup=clients_menu_kb())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.effective_message.text or "").strip()

    # 1) /find awaiting text
    if _is_admin(update.effective_user.id) and context.user_data.pop(FIND_AWAIT, False):
        return await process_find_text(update, context, raw)

    # 2) Admin ReplyKeyboard
    if _is_admin(update.effective_user.id) and raw == BTN_ADM_SEARCH:
        return await admin_search_entry(update, context)
    if _is_admin(update.effective_user.id) and raw == BTN_ADM_CLIENTS:
        return await ask_clients_menu(update, context)

    # 3) User menu
    if raw == BTN_TRACK:
        return await say(update, context, "Пришлите номер заказа (order_id) или используйте /find (для админов).")
    if raw == BTN_ADDR:
        # Show own profile
        user = update.effective_user
        profile = sheets.get_client_by_username(user.username or "")
        if profile:
            txt = (
                f"*Ваш профиль:*\n"
                f"username: @{profile.get('username','')}\n"
                f"ФИО: {profile.get('full_name','')}\n"
                f"Телефон: {profile.get('phone','')}\n"
                f"Город: {profile.get('city','')}\n"
                f"Адрес: {profile.get('address','')}\n"
                f"Индекс: {profile.get('postcode','')}\n"
            )
            return await say_md(update, context, txt)
        else:
            return await say(update, context, "Профиль не найден. Попросите администратора добавить/изменить данные.")

# callbacks
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    if data.startswith("find:open:"):
        _,_,oid = data.split(":",2)
        return await open_order_card(update, context, oid)

    if data.startswith("find:page:"):
        _,_,page_s = data.split(":",2)
        page = int(page_s)
        items = context.user_data.get(FIND_RESULTS, [])
        context.user_data[FIND_PAGE] = page
        kb = build_results_kb(items, page)
        return await q.edit_message_reply_markup(reply_markup=kb)

    if data == "clients:export":
        import pandas as pd
        df = sheets.export_clients_dataframe()
        if df is None or df.empty:
            return await q.edit_message_text("Пока нет клиентов.")
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        bio = io.BytesIO(buf.getvalue().encode("utf-8"))
        bio.name = "clients_export.csv"
        await update.effective_message.reply_document(bio, caption="Экспорт клиентов (CSV)")
        return

    if data == "clients:edit":
        if not _is_admin(update.effective_user.id):
            return
        context.user_data[EDIT_STAGE] = "await_username"
        context.user_data[EDIT_BUF] = {}
        return await q.edit_message_text("Введите @username клиента, которого нужно изменить (или создать):")

    if data == "clients:back":
        return await q.edit_message_text("Готово. Возврат в админ-панель. Откройте /admin", reply_markup=None)

async def on_text_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A lightweight state machine for admin client edit."""
    stage = context.user_data.get(EDIT_STAGE)
    if not stage:
        return False

    msg = update.effective_message
    text = (msg.text or "").strip()

    if stage == "await_username":
        uname = text.lstrip("@")
        buf = context.user_data.get(EDIT_BUF, {})
        buf["username"] = uname
        # preload existing
        ex = sheets.get_client_by_username(uname)
        buf["full_name"] = ex.get("full_name","") if ex else ""
        buf["phone"] = ex.get("phone","") if ex else ""
        buf["city"] = ex.get("city","") if ex else ""
        buf["address"] = ex.get("address","") if ex else ""
        buf["postcode"] = ex.get("postcode","") if ex else ""
        context.user_data[EDIT_BUF] = buf
        context.user_data[EDIT_STAGE] = "await_full_name"
        await say(update, context, f"ФИО [{buf['full_name']}]: (введите новое или '-' чтобы оставить)")
        return True

    buf = context.user_data.get(EDIT_BUF, {})
    def keep_or(val, prev): return prev if val == "-" else val

    if stage == "await_full_name":
        buf["full_name"] = keep_or(text, buf.get("full_name",""))
        context.user_data[EDIT_STAGE] = "await_phone"
        await say(update, context, f"Телефон [{buf.get('phone','')}]: ")
        return True

    if stage == "await_phone":
        buf["phone"] = keep_or(text, buf.get("phone",""))
        context.user_data[EDIT_STAGE] = "await_city"
        await say(update, context, f"Город [{buf.get('city','')}]: ")
        return True

    if stage == "await_city":
        buf["city"] = keep_or(text, buf.get("city",""))
        context.user_data[EDIT_STAGE] = "await_address"
        await say(update, context, f"Адрес [{buf.get('address','')}]: ")
        return True

    if stage == "await_address":
        buf["address"] = keep_or(text, buf.get("address",""))
        context.user_data[EDIT_STAGE] = "await_postcode"
        await say(update, context, f"Индекс [{buf.get('postcode','')}]: ")
        return True

    if stage == "await_postcode":
        buf["postcode"] = keep_or(text, buf.get("postcode",""))
        # save
        user = update.effective_user
        sheets.upsert_client_profile(user_id=user.id, username=buf["username"], full_name=buf["full_name"],
                                     phone=buf["phone"], city=buf["city"], address=buf["address"], postcode=buf["postcode"])
        context.user_data.pop(EDIT_STAGE, None)
        context.user_data.pop(EDIT_BUF, None)
        await say(update, context, "Сохранено ✅")
        return True

    return False

# ========== Registration ==========
def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("find", find_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_for_edit))
    app.add_handler(CallbackQueryHandler(on_cb))
