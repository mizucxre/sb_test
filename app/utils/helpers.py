import asyncio
import logging
from typing import List, Tuple
from telegram import Update, ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def _typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float = 0.6):
    """Эффект печати"""
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)

async def reply_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    """Ответ с анимацией печати"""
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id)
    return await msg.reply_text(text, **kwargs)

async def reply_markdown_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    """Ответ с анимацией и Markdown"""
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id)
    return await msg.reply_markdown(text, **kwargs)

def _slice_page(items: List, page: int, per_page: int) -> Tuple[List, int]:
    """Пагинация списка"""
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start:start + per_page], total_pages

def build_participants_text(order_id: str, participants: List, page: int, per_page: int = 8) -> str:
    """Текст списка участников с пагинацией"""
    from app.utils.helpers import _slice_page
    
    slice_, total_pages = _slice_page(participants, page, per_page)
    lines = [f"*Разбор* `{order_id}` — участники ({page+1}/{total_pages}):"]
    
    if not slice_:
        lines.append("_Список участников пуст._")
    
    for p in slice_:
        mark = "✅" if p.paid else "❌"
        lines.append(f"{mark} @{p.username}")
    
    return "\n".join(lines)

def _is_admin(uid, admin_ids) -> bool:
    """Проверка является ли пользователь админом"""
    return uid in admin_ids or str(uid) in {str(x) for x in admin_ids}

def _err_reason(e: Exception) -> str:
    """Определение причины ошибки отправки сообщения"""
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
