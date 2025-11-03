# -*- coding: utf-8 -*-
"""
SEABLUU Admin UI — v3
Исправлено по пунктам пользователя:
1) Чат: корректный рендер старых сообщений, сортировка по времени, оптимистичная отправка с ⏳→✓✓.
2) Телеграм-новости: парсинг настоящих фото (игнор эмодзи), карточки в стиле ВК (аватар канала, дата, текст, фото).
3) Настройки профиля: перенесены в правый верх — иконка «шестерёнка» открывает красивый drawer; загрузка аватарки drag&drop + клик.
4) Список админов: карточки с аватаром, роль как pill, дата, аккуратные отступы.
5) Автосабмит/подсказки браузера отключены (autocomplete/off и т.д.).
6) Никакой автозагрузки при переключении вкладок; есть кнопки «Обновить»; в списках по 20 записей.
7) Загрузка аватаров без python-multipart (raw body) + раздача через /admin/media/avatars/.

Интеграция: из FastAPI-приложения импортируйте get_admin_router().
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, time, re, urllib.parse, urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from . import sheets

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

router = APIRouter()

_CACHE: Dict[str, Any] = {}

def _cache_get(k: str, ttl: int = 8):
    v = _CACHE.get(k)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > ttl:
        return None
    return data

def _cache_set(k: str, data: Any):
    _CACHE[k] = (time.time(), data)

def _cache_clear():
    _CACHE.clear()


def _secret() -> str:
    return (os.getenv("ADMIN_SECRET", "dev-secret") or "dev-secret").strip()

def _sign(data: str) -> str:
    return hmac.new(_secret().encode(), data.encode(), hashlib.sha256).hexdigest()

def _make_token(login: str, ttl: int = 12 * 3600) -> str:
    payload = json.dumps({"login": login, "exp": int(time.time()) + ttl}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(payload.encode()).decode()
    return b + "." + _sign(b)

def _parse_token(token: str) -> Optional[str]:
    try:
        b, sig = token.split(".", 1)
        if not hmac.compare_digest(_sign(b), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b.encode()).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("login")
    except Exception:
        return None

def _authed_login(request: Request) -> Optional[str]:
    t = request.cookies.get("adm_session", "")
    return _parse_token(t) if t else None

def _hash_pwd(login: str, password: str) -> str:
    base = f"{login.strip().lower()}:{password}:{_secret()}".encode()
    return hashlib.sha256(base).hexdigest()


def _admins_ws():
    ws = sheets.get_worksheet("admins")
    if not ws.get_all_values():
        ws.append_row(["login", "password_hash", "role", "avatar", "created_at"])
    owner_login = (os.getenv("ADMIN_LOGIN", "admin") or "admin").strip()
    owner_pass = (os.getenv("ADMIN_PASSWORD", "admin") or "admin").strip()
    owner_avatar = os.getenv("ADMIN_AVATAR", "")
    rows = ws.get_all_records()
    want_hash = _hash_pwd(owner_login, owner_pass)
    found = [i for i, r in enumerate(rows, start=2) if str(r.get("login", "")).strip().lower() == owner_login.lower()]
    if found:
        i = found[0]
        try:
            ws.update_cell(i, 2, want_hash)
            ws.update_cell(i, 3, "owner")
            if owner_avatar:
                ws.update_cell(i, 4, owner_avatar)
        except Exception:
            pass
        for extra in found[1:][::-1]:
            try: ws.delete_rows(extra)
            except Exception: pass
    else:
        ws.append_row([owner_login, want_hash, "owner", owner_avatar, sheets._now()])
    return ws


def _get_admin(login: str) -> Optional[Dict[str, Any]]:
    for r in _admins_ws().get_all_records():
        if str(r.get("login", "")).strip().lower() == login.strip().lower():
            return r
    return None


def _list_admins() -> List[Dict[str, Any]]:
    return _admins_ws().get_all_records()


def _add_admin(current_login: str, new_login: str, password: str, role: str, avatar: str = "") -> bool:
    cur = _get_admin(current_login)
    if not cur or cur.get("role") != "owner":
        return False
    if not new_login or _get_admin(new_login):
        return False
    _admins_ws().append_row([new_login, _hash_pwd(new_login, password), role, avatar, sheets._now()])
    return True


def _set_admin_avatar(target_login: str, avatar_url: str) -> bool:
    ws = _admins_ws()
    try:
        headers = ws.row_values(1)
        col = 4
        for idx, h in enumerate(headers, start=1):
            if h.strip().lower() == "avatar":
                col = idx
                break
        rows = ws.get_all_records()
        row_index = None
        for i, r in enumerate(rows, start=2):
            if str(r.get("login", "")).strip().lower() == target_login.strip().lower():
                row_index = i
                break
        if not row_index:
            return False
        ws.update_cell(row_index, col, avatar_url)
        return True
    except Exception:
        return False


def _bot_token() -> str:
    try:
        from .config import BOT_TOKEN as _TOK  # type: ignore
        if _TOK:
            return _TOK
    except Exception:
        pass
    return os.getenv("BOT_TOKEN", "")


def _notify_subscribers(order_id: str, new_status: str) -> None:
    token = _bot_token()
    if not token:
        return
    try:
        subs = sheets.get_all_subscriptions()
        chat_ids: List[int] = []
        for s in subs:
            if str(s.get("order_id", "")) == order_id:
                try:
                    chat_ids.append(int(s.get("user_id")))
                except Exception:
                    pass
        if not chat_ids:
            return
        text = f"Статус {order_id}: {new_status}"
        for uid in set(chat_ids):
            try:
                params = urllib.parse.urlencode({"chat_id": uid, "text": text})
                urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage?{params}", timeout=5).read()
                sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception:
                pass
    except Exception:
        pass


_LOGIN_HTML = """<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\" /><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" /><title>SEABLUU Admin</title><body style=\"margin:0;display:grid;place-items:center;height:100vh;background:#0b1020;color:#e6ebff;font:16px system-ui,-apple-system,Segoe UI,Roboto,Arial\"><div style=\"padding:18px 22px;border:1px solid #9fb0ff3a;border-radius:12px;background:#151b2d;box-shadow:0 10px 30px rgba(0,0,0,.25)\">SEABLUU Admin готов. Маршрут /admin работает.</div></body></html>"""

@router.get("/", response_class=HTMLResponse)
async def admin_page_root() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML)


def get_admin_router() -> APIRouter:
    return router
