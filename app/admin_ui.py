# -*- coding: utf-8 -*-
"""
SEABLUU Admin UI — v4 (mobile-friendly left drawer)

Что сделано по пунктам пользователя:
1) Чат: показ «старых» сообщений (полная лента, сортировка по id/времени), оптимистичная отправка, ✓✓, автообновление по чекбоксу.
2) Новости: чтение из листа `news` + форма публикации новости (только роль owner), мгновенное появление в ленте.
3) Настройки: шторка закрывается по крестику, по ESC и по клику на подложке.
4) Навигация: все разделы перенесены в левую «телега-шторку» (бургер), удобно на телефоне.
5) Создание разборов: рабочий POST `/admin/api/orders` — пишет в лист `orders`, сразу видно в «Заказы»; попытка подписать клиентов и отправить уведомление (мягко, без падений).
6) Массовое изменение статусов: чекбоксы в списке заказов + форма «Применить к выбранным» → `/admin/api/status/bulk`.
7) Адреса: к каждому адресу добавлен номер телефона (если есть).
8) Клиенты: у клиента показываются его заказы (по username).
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, time, re, urllib.parse, urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import threading

from . import sheets

# Статусы (список можно расширить/подправить — индексы стабильны)
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

# ------------------------ util / auth / cache ------------------------
def _cache_get(k: str, ttl: int = 8):
    v = _CACHE.get(k)
    if not v: return None
    ts, data = v
    if time.time() - ts > ttl: return None
    return data

def _cache_set(k: str, data: Any):
    _CACHE[k] = (time.time(), data)

def _cache_clear():
    _CACHE.clear()

# --- Fast chat storage (JSONL, без Google Sheets) ---
CHAT_FILE = os.path.join(os.getcwd(), "media", "chat.jsonl")
CHAT_MAX = 2000  # сколько последних сообщений держать в памяти

_chat_lock = threading.RLock()
_chat: List[Dict[str, Any]] = []

def _ensure_media_dir():
    os.makedirs(os.path.dirname(CHAT_FILE), exist_ok=True)

def _chat_load():
    global _chat
    _ensure_media_dir()
    items: List[Dict[str, Any]] = []
    if os.path.isfile(CHAT_FILE):
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "id" in obj:
                        items.append(obj)
                except Exception:
                    continue
    _chat = items[-CHAT_MAX:]

def _chat_next_id() -> int:
    if not _chat:
        return 1
    try:
        return int(_chat[-1]["id"]) + 1
    except Exception:
        return len(_chat) + 1

def _chat_append(msg: Dict[str, Any]) -> None:
    with _chat_lock:
        _chat.append(msg)
        if len(_chat) > CHAT_MAX:
            _chat[:] = _chat[-CHAT_MAX:]
        try:
            with open(CHAT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except Exception:
            pass

_chat_load()

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
        if not hmac.compare_digest(_sign(b), sig): return None
        payload = json.loads(base64.urlsafe_b64decode(b.encode()).decode())
        if payload.get("exp", 0) < time.time(): return None
        return payload.get("login")
    except Exception:
        return None

def _authed_login(request: Request) -> Optional[str]:
    t = request.cookies.get("adm_session", "")
    return _parse_token(t) if t else None

def _hash_pwd(login: str, password: str) -> str:
    base = f"{login.strip().lower()}:{password}:{_secret()}".encode()
    return hashlib.sha256(base).hexdigest()

# ------------------------ admins helpers ------------------------
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
                col = idx; break
        rows = ws.get_all_records()
        row_index = None
        for i, r in enumerate(rows, start=2):
            if str(r.get("login", "")).strip().lower() == target_login.strip().lower():
                row_index = i; break
        if not row_index: return False
        ws.update_cell(row_index, col, avatar_url)
        return True
    except Exception:
        return False

# ------------------------ Telegram helpers ------------------------
def _bot_token() -> str:
    try:
        from .config import BOT_TOKEN as _TOK  # type: ignore
        if _TOK: return _TOK
    except Exception:
        pass
    return os.getenv("BOT_TOKEN", "")

def _send_tg(uid: int, text: str) -> None:
    token = _bot_token()
    if not token or not uid:
        return
    try:
        params = urllib.parse.urlencode({"chat_id": uid, "text": text})
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage?{params}", timeout=5).read()
    except Exception:
        pass

def _notify_subscribers(order_id: str, new_status: str) -> None:
    token = _bot_token()
    if not token: return
    try:
        subs = sheets.get_all_subscriptions()
        chat_ids: List[int] = []
        for s in subs:
            if str(s.get("order_id", "")) == order_id:
                try: chat_ids.append(int(s.get("user_id")))
                except Exception: pass
        if not chat_ids: return
        text = f"Статус {order_id}: {new_status}"
        for uid in set(chat_ids):
            _send_tg(uid, text)
            try: sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception: pass
    except Exception:
        pass

def _resolve_username_to_uid(username: str) -> Optional[int]:
    # мягкая попытка найти chat_id по username — если функции нет, просто None
    try:
        return sheets.resolve_username_to_chat_id(username)  # type: ignore[attr-defined]
    except Exception:
        try:
            rec = sheets.get_user_by_username(username)  # type: ignore[attr-defined]
            if rec and rec.get("user_id"): return int(rec["user_id"])
        except Exception:
            pass
    return None

# ------------------------ HTML ------------------------
_LOGIN_HTML = r"""
<!doctype html>
<html lang="ru">
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SEABLUU — Вход</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; --accent:#4f5fff; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial;display:grid;place-items:center;height:100vh}
  .card{width:min(440px,92vw);padding:22px;border:1px solid var(--muted);border-radius:14px;background:var(--card);box-shadow:0 10px 30px rgba(0,0,0,.25)}
  input{display:block;width:100%;padding:12px 14px;border:1px solid var(--muted);border-radius:12px;background:#1c233b;color:#e6ebff}
  button{display:block;width:100%;padding:12px 16px;border-radius:12px;border:1px solid var(--muted);background:#2b3961;color:#e6ebff;cursor:pointer}
  h1{margin:0 0 14px 0;font-size:18px}
  .gap{height:10px}
  .err{color:#ff9aa2;font-size:13px;min-height:16px}
</style>
<div class="card">
  <h1>SEABLUU — Вход</h1>
  <div class="err" id="err"></div>
  <input id="login" placeholder="Логин" autocomplete="username" />
  <div class="gap"></div>
  <input id="pwd" type="password" placeholder="Пароль" autocomplete="current-password" />
  <div class="gap"></div>
  <button id="btnLogin" onclick="doLogin()">Войти</button>
</div>
<script>
async function doLogin(){
  const btn=document.getElementById('btnLogin'); let old='Войти'; if(btn){ old=btn.textContent; btn.disabled=true; btn.textContent='Входим…'; }
  try{
    const login=document.getElementById('login').value.trim();
    const password=document.getElementById('pwd').value;
    const r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login,password})});
    const j=await r.json(); if(!j.ok){ document.getElementById('err').innerText=j.error||'Ошибка входа'; return; }
    location.reload();
  } finally { if(btn){ btn.disabled=false; btn.textContent=old; } }
}
</script>
</html>
"""

def _owner_js_flag(login: str) -> str:
    role = (_get_admin(login) or {}).get("role","")
    return "true" if role == "owner" else "false"

def _admin_page_html(user_login: str) -> str:
    # Левый drawer + контент
    return r"""
<!doctype html>
<html lang="ru">
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SEABLUU — Админ-панель</title>
<style>
  :root{--bg:#0b1020;--card:#151b2d;--ink:#e6ebff;--muted:#9fb0ff3a;--accent:#4f5fff}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial}
  header{height:56px;display:flex;align-items:center;gap:10px;padding:0 12px;border-bottom:1px solid var(--muted);position:sticky;top:0;background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85));backdrop-filter:blur(6px);z-index:10}
  .burger{width:28px;height:28px;border:1px solid var(--muted);border-radius:8px;display:grid;place-items:center;cursor:pointer;background:#1c233b}
  .wrap{display:grid;grid-template-columns: 1fr;max-width:1100px;margin:0 auto;padding:12px}
  .content{min-height:calc(100vh - 56px);padding:10px}
  input,select,textarea{padding:10px 12px;border:1px solid var(--muted);border-radius:10px;background:#1c233b;color:#e6ebff}
  button{padding:10px 12px;border:1px solid var(--muted);border-radius:10px;background:#2b3961;color:#e6ebff;cursor:pointer}
  .muted{color:#c7d2fecc;font-size:13px}
  .list{display:grid;gap:10px}
  .item{padding:12px;border:1px solid var(--muted);border-radius:12px;background:var(--card)}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .avatar{width:28px;height:28px;border-radius:50%;object-fit:cover;border:1px solid var(--muted);background:#111}
  .avatar.lg{width:46px;height:46px}
  .pill{padding:6px 10px;border:1px solid var(--muted);border-radius:999px;background:#1c233b;color:var(--ink);font-size:13px}
  .toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%) translateY(20px);opacity:0;background:#1c233b;color:#e6ebff;border:1px solid var(--muted);padding:10px 14px;border-radius:12px;transition:all .35s ease;box-shadow:0 10px 20px rgba(0,0,0,.25);z-index:100}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .overlay{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.35);z-index:60}
  .overlay.show{display:flex}
  .spinner{background:#1c233b;border:1px solid var(--muted);color:#e6ebff;padding:10px 14px;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,.35)}
  .drawer{position:fixed;left:-280px;top:0;width:260px;height:100vh;background:#141a2d;border-right:1px solid var(--muted);box-shadow:12px 0 24px rgba(0,0,0,.25);transition:left .25s ease;z-index:70;padding:12px}
  .drawer.show{left:0}
  .nav a{display:block;padding:10px 12px;border-radius:10px;color:#e6ebff;text-decoration:none;border:1px solid transparent}
  .nav a.active,.nav a:hover{background:#1c233b;border-color:var(--muted)}
  .news-card{padding:12px;border:1px solid var(--muted);border-radius:12px;background:#141a2d}
  .news-head{display:flex;gap:10px;align-items:center}
  .news-ava{width:40px;height:40px;border-radius:50%;object-fit:cover;border:1px solid var(--muted);background:#111}
  .news-img{width:100%;max-height:440px;object-fit:cover;border-radius:10px;border:1px solid var(--muted);background:#111;margin-top:8px}
  .chat-wrap{max-width:920px;margin:0 auto;display:grid;gap:8px}
  .messages{height:60vh;min-height:360px;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:6px}
  .msg{display:flex;gap:8px;align-items:flex-end;max-width:80%}
  .msg .bubble{background:#1e2a49;border:1px solid var(--muted);padding:8px 10px;border-radius:14px 14px 14px 4px;white-space:pre-wrap;position:relative}
  .msg.me{margin-left:auto;flex-direction:row-reverse}
  .msg.me .bubble{background:#294172;border-color:#3b4f83;border-radius:14px 14px 4px 14px}
  .meta{font-size:12px;color:#c7d2fe99;margin-top:2px}
  .tick{position:absolute;right:6px;bottom:-16px;font-size:12px;color:#9fb0ff99}
  .sending::after{content:'⏳';position:absolute;right:6px;bottom:-16px;font-size:12px;opacity:.9}
  .failed{border-color:#ff7b7b!important}
  .admin-card{display:grid;grid-template-columns:52px 1fr;gap:12px;align-items:center;padding:10px;border:1px solid var(--muted);border-radius:12px;background:#1b233b}
  .role{font-size:12px;border:1px solid var(--muted);padding:2px 6px;border-radius:999px;margin-left:6px;opacity:.9}
  .closeX{margin-left:auto;border-radius:8px;background:#1c233b;border:1px solid var(--muted)}
  .toolbar{display:flex;gap:8px;align-items:center;margin:8px 0}
  .checkbox{transform:scale(1.2)}
</style>

<header>
  <div class="burger" id="btnBurger">☰</div>
  <img id="header_avatar" class="avatar" src="" alt=""/>
  <div style="flex:1"></div>
  <button class="closeX" id="btnSettings">⚙️</button>
  <button onclick="logout()">Выйти</button>
</header>

<aside class="drawer" id="leftDrawer">
  <div class="row" style="align-items:center">
    <img id="me_preview" class="avatar lg" src="" alt=""/>
    <div style="font-weight:600;margin-left:6px">class="muted" id="me_login"</div>
    <button class="closeX" id="btnCloseDrawer" title="Закрыть">✕</button>
  </div>
  <div style="height:10px"></div>
  <nav class="nav">
    <a href="#tab_home">🏠 Главная</a>
    <a href="#tab_orders">📦 Заказы</a>
    <a href="#tab_create">➕ Создать разбор</a>
    <a href="#tab_clients">👥 Клиенты</a>
    <a href="#tab_addresses">📮 Адреса</a>
    <a href="#tab_admins">🛠 Админы</a>
    <a href="#tab_chat">💬 Чат</a>
  </nav>
  <div style="height:8px"></div>
  <div class="news-card">
    <div style="font-weight:600;margin-bottom:6px">Профиль</div>
    <input id="me_avatar" placeholder="avatar URL" autocomplete="off"/>
    <div class="row" style="margin-top:6px">
      <input id="me_file" type="file" accept="image/*" style="display:none"/>
      <button onclick="document.getElementById('me_file').click()">Загрузить</button>
      <button onclick="saveMyAvatar()">Сохранить</button>
    </div>
  </div>
</aside>

<div class="wrap">
  <div class="content">

    <section id="tab_home" class="item">
      <div class="row toolbar">
        <button onclick="loadNews(true)">Обновить новости</button>
        <span class="muted">Покажем 5 последних постов</span>
      </div>
      <div id="news" class="list"></div>
      <div id="newsForm" class="news-card" style="margin-top:12px;display:none">
        <div style="font-weight:600;margin-bottom:6px">Опубликовать новость (только owner)</div>
        <input id="nw_text" placeholder="Текст…" autocomplete="off"/>
        <div class="row" style="margin-top:6px">
          <input id="nw_image" placeholder="URL картинки (опц.)" style="flex:1" autocomplete="off"/>
          <button onclick="publishNews()">Опубликовать</button>
        </div>
        <div class="muted" style="margin-top:6px">Новости берём из листа <b>news</b>; публикация добавляет запись туда.</div>
      </div>
    </section>

    <section id="tab_orders" class="item" style="margin-top:12px">
      <div class="row toolbar">
        <input id="q" placeholder="order_id / @username / телефон" autocomplete="off" autocapitalize="off" spellcheck="false"/>
        <button onclick="loadOrders(true)">Обновить</button>
        <label class="row pill" style="gap:6px"><input id="autoOrders" type="checkbox" onchange="toggleAutoOrders()"> автообновление</label>
      </div>
      <div class="row" style="margin:6px 0">
        <select id="bulk_status_pick">
          """ + "".join([f'<option value="adm:pick_status_id:{i}">{s}</option>' for i, s in enumerate(STATUSES)]) + r"""
        </select>
        <button onclick="applyBulk()">Применить к выбранным</button>
        <span class="muted">Выберите заказы чекбоксами ниже</span>
      </div>
      <div id="orders" class="list"></div>
    </section>

    <section id="tab_create" class="item" style="margin-top:12px">
      <div class="row">
        <input id="c_order_id" placeholder="только цифры (например 12345)" inputmode="numeric" autocomplete="off" oninput="this.value=this.value.replace(/\D+/g,'')"/>
        <select id="c_origin"><option>CN</option><option>KR</option></select>
        <select id="c_status">
          """ + "".join([f'<option value="adm:pick_status_id:{i}">{s}</option>' for i, s in enumerate(STATUSES)]) + r"""
        </select>
      </div>
      <div class="row" style="margin-top:6px">
        <input id="c_clients" placeholder="клиенты через запятую (@user1, @user2)" style="min-width:320px" autocomplete="off"/>
        <input id="c_note" placeholder="заметка" style="min-width:220px" autocomplete="off"/>
        <button id="btnCreate" onclick="createOrder()">Создать</button>
      </div>
    </section>

    <section id="tab_clients" class="item" style="margin-top:12px">
      <div class="row"><button onclick="loadClients(true)">Обновить</button><span class="muted">До 20 записей</span></div>
      <div id="clients" class="list"></div>
    </section>

    <section id="tab_addresses" class="item" style="margin-top:12px">
      <div class="row">
        <input id="aq" placeholder="username для фильтра (опц.) — без @" autocomplete="off"/>
        <button onclick="loadAddresses(true)">Обновить</button><span class="muted">До 20</span>
      </div>
      <div id="addresses" class="list"></div>
    </section>

    <section id="tab_admins" class="item" style="margin-top:12px">
      <div class="row"><button onclick="loadAdmins(true)">Обновить список</button></div>
      <div id="admins" class="list"></div>
    </section>

    <section id="tab_chat" class="item" style="margin-top:12px">
      <div class="chat-wrap">
        <div class="row">
          <button onclick="loadChat(true,true)">Обновить чат</button>
          <label class="row pill" style="gap:6px"><input id="autoChat" type="checkbox" onchange="toggleAutoChat()"> автообновление</label>
        </div>
        <div id="messages" class="messages"></div>
        <div class="row" style="position:sticky;bottom:0;background:linear-gradient(0deg,rgba(11,16,32,1),rgba(11,16,32,.8));padding-top:8px">
          <input id="ch_text" placeholder="Сообщение… (Enter — отправить)" style="flex:1" autocomplete="off" />
          <input id="ch_ref" placeholder="Привязка: @username или CN-12345" style="min-width:220px" autocomplete="off"/>
          <button id="btnSend" onclick="sendMsg()">Отправить</button>
        </div>
      </div>
    </section>

  </div>
</div>

<div id="overlay" class="overlay"><div class="spinner">Загрузка…</div></div>
<div id="backdrop" class="overlay"></div>
<div id="toast" class="toast"></div>

<script>
const IS_OWNER = __IS_OWNER__;
const STATUSES = __STATUSES__;
let ME = {login:'', avatar:'', role:''};
let __lastMsgId = 0;
let __pending=0; let __chatTimer=null; let __ordersTimer=null;

function overlay(show){ const ov=document.getElementById('overlay'); if(!ov) return; ov.classList[show?'add':'remove']('show'); }
async function api(path, opts={}, showSpinner=false){
  if(showSpinner){ __pending++; if(__pending===1) overlay(true); }
  try{
    const r = await fetch('/admin'+path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
    const text = await r.text();
    let data; try{ data = JSON.parse(text); } catch(e){ data = {ok:false, error:'bad_json', status:r.status, raw:text.slice(0,200)}; }
    if(!r.ok){ data = Object.assign({ok:false}, data||{}); if(!data.error) data.error = 'HTTP '+r.status; }
    return data;
  } catch(e){ return {ok:false, error: (e && e.message) || 'network_error'}; }
  finally { if(showSpinner){ __pending--; if(__pending<=0) overlay(false); } }
}

function toast(msg){ const el=document.getElementById('toast'); el.textContent=String(msg||''); el.classList.add('show'); setTimeout(()=>el.classList.remove('show'), 2000); }
function statusName(x){ if(!x) return '—'; if(x.includes('pick_status_id')){ const i=parseInt(x.replace(/[^0-9]/g,'')); if(!isNaN(i)&&i>=0&&i<STATUSES.length) return STATUSES[i]; } return x; }
function fmtTime(s){ if(!s) return ''; const d=new Date(s); if(isNaN(+d)) return s; return d.toLocaleString(); }

// --- left drawer & settings closing ---
(function(){
  function setActiveFromHash(){
    var id=location.hash||'#tab_home'; var sections=document.querySelectorAll('.content > section');
    for(var i=0;i<sections.length;i++){ sections[i].style.display='none'; }
    var el=document.querySelector(id); if(el) el.style.display='block';
    // подсветка в навигации
    var links=document.querySelectorAll('.nav a');
    for(var j=0;j<links.length;j++){ links[j].classList.toggle('active', links[j].getAttribute('href')===id); }
  }
  window.addEventListener('hashchange', setActiveFromHash);
  if(document.readyState==='loading') window.addEventListener('DOMContentLoaded', setActiveFromHash); else setActiveFromHash();

  window.logout = function(){ fetch('/admin/api/logout',{method:'POST'}).then(function(){ location.reload(); }); };

  const drawer=document.getElementById('leftDrawer');
  const backdrop=document.getElementById('backdrop');
  const burger=document.getElementById('btnBurger');
  const btnClose=document.getElementById('btnCloseDrawer');
  const btnSettings=document.getElementById('btnSettings');

  function openDrawer(){ drawer.classList.add('show'); backdrop.classList.add('show'); }
  function closeDrawer(){ drawer.classList.remove('show'); backdrop.classList.remove('show'); }
  if(burger) burger.onclick=openDrawer;
  if(btnClose) btnClose.onclick=closeDrawer;
  if(backdrop) backdrop.onclick=closeDrawer;
  if(btnSettings) btnSettings.onclick=openDrawer; // настройки теперь внутри левой шторки

  document.addEventListener('keydown', function(e){
    if(e.key==='Escape'){ closeDrawer(); }
  });

  const me_file=document.getElementById('me_file');
  if(me_file){ me_file.onchange=function(){ if(me_file.files && me_file.files[0]) uploadAvatarRaw(me_file.files[0],'me_avatar','me_preview',true); }; }
})();

// --- orders list + bulk ---
function renderOrders(items){
  const list = document.getElementById('orders'); list.innerHTML='';
  if(!items.length){ list.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const o of items){
    const div=document.createElement('div'); div.className='item';
    const dt=(o.updated_at||'').replace('T',' ').slice(0,16);
    const opts = STATUSES.map((s,i)=>`<option value="adm:pick_status_id:${i}" ${statusName(o.status)===s?'selected':''}>${s}</option>`).join('');
    div.innerHTML=`
      <div class="row" style="justify-content:space-between">
        <label class="row" style="gap:8px">
          <input class="checkbox" type="checkbox" data-oid="${o.order_id}"/>
          <span class="pill">${o.order_id||''}</span>
        </label>
        <span class="muted">${(o.origin||o.country||'—').toUpperCase()} · ${dt||'—'} · ${o.client_name||''}</span>
      </div>
      <div class="row" style="margin-top:8px">
        <select id="pick_${o.order_id}">${opts}</select>
        <button onclick="saveStatus('${o.order_id}', this)">Сохранить статус</button>
      </div>`;
    list.appendChild(div);
  }
}
async function loadOrders(sp){
  const q = (document.getElementById('q')||{value:''}).value.trim();
  const data = await api('/api/search?q='+encodeURIComponent(q), {}, sp);
  if(!data || data.ok===false){ document.getElementById('orders').innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  renderOrders((data.items||[]).slice(0,50));
}
async function saveStatus(oid, btn){
  if(btn) btn.disabled=true; try{
    const sel=document.getElementById('pick_'+CSS.escape(oid));
    const status=sel.value;
    const res=await api('/api/status',{method:'POST',body:JSON.stringify({order_id:oid,status})}, true);
    toast(res && res.ok!==false?'Статус сохранён':(res.error||'Ошибка'));
  } finally { if(btn) btn.disabled=false; }
}
function toggleAutoOrders(){ const ch=document.getElementById('autoOrders'); if(!ch) return; if(__ordersTimer){ clearInterval(__ordersTimer); __ordersTimer=null; } if(ch.checked){ __ordersTimer=setInterval(()=>loadOrders(false), 5000); } }
async function applyBulk(){
  const status=document.getElementById('bulk_status_pick').value;
  const cbs=[...document.querySelectorAll('input.checkbox[data-oid]')].filter(cb=>cb.checked);
  if(!cbs.length){ toast('Выберите заказы'); return; }
  const ids=cbs.map(cb=>cb.getAttribute('data-oid'));
  const r=await api('/api/status/bulk',{method:'POST',body:JSON.stringify({order_ids:ids,status})}, true);
  toast(r.ok?('Готово: '+(r.updated||0)):(r.error||'Ошибка'));
  if(r.ok) loadOrders(false);
}

// --- create order ---
async function createOrder(){
  const b=document.getElementById('btnCreate'); if(b) b.disabled=true; try{
    const origin=document.getElementById('c_origin').value.trim().toUpperCase();
    const idnum=(document.getElementById('c_order_id').value.trim()).replace(/\D+/g,'');
    if(!idnum){ toast('Введите цифры номера заказа'); return; }
    const order_id=origin+'-'+idnum;
    const status=document.getElementById('c_status').value;
    const clients=document.getElementById('c_clients').value.trim();
    const note=document.getElementById('c_note').value.trim();
    const r=await api('/api/orders',{method:'POST',body:JSON.stringify({order_id,origin,status,clients,note})}, true);
    toast(r.ok?'Разбор создан':(r.error||'Ошибка'));
    if(r.ok){ location.hash='#tab_orders'; loadOrders(false); }
  } finally{ if(b) b.disabled=false; }
}

// --- clients ---
async function loadClients(sp){
  const data=await api('/api/clients',{method:'GET'}, sp);
  const box=document.getElementById('clients'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=(data.items||[]).slice(0,20);
  if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const u of arr){
    const div=document.createElement('div'); div.className='item';
    div.innerHTML=`<div class="row" style="justify-content:space-between">
      <div><b>${u.username||u.name||''}</b> <span class="muted">${u.phone||''}</span></div>
      </div>
      <div class="muted">Заказы: ${(u.orders||[]).map(o=>o.order_id).join(', ') || '—'}</div>`;
    box.appendChild(div);
  }
}

// --- addresses ---
async function loadAddresses(sp){
  const q=(document.getElementById('aq')||{value:''}).value.trim();
  const data=await api('/api/addresses?q='+encodeURIComponent(q), {method:'GET'}, sp);
  const box=document.getElementById('addresses'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=(data.items||[]).slice(-20);
  if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const a of arr){
    const div=document.createElement('div'); div.className='item';
    div.innerHTML=`<div class="row" style="justify-content:space-between">
      <div>${a.username?('@'+a.username):'—'}</div>
      <div class="muted">${a.phone||''}</div>
    </div>
    <div class="muted">${a.address||''}</div>`;
    box.appendChild(div);
  }
}

// --- admins ---
async function loadAdmins(sp){
  const data=await api('/api/admins',{method:'GET'}, sp);
  const box=document.getElementById('admins'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=data.items||[]; if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const a of arr){
    const card=document.createElement('div'); card.className='admin-card';
    const img=document.createElement('img'); img.className='avatar lg'; img.src=a.avatar||''; img.alt='';
    const right=document.createElement('div');
    const title=document.createElement('div'); title.innerHTML='<b>'+a.login+'</b><span class="role">'+(a.role||'')+'</span>';
    const sub=document.createElement('div'); sub.className='muted'; sub.textContent=a.created_at||'';
    right.appendChild(title); right.appendChild(sub);
    card.appendChild(img); card.appendChild(right);
    box.appendChild(card);
  }
}

// --- chat ---
function renderMessages(items){
  const box = document.getElementById('messages');
  const me = ME.login || '';
  items.sort(function(a,b){
    const ai=parseInt(String(a.id||'0')); const bi=parseInt(String(b.id||'0'));
    if(!isNaN(ai) && !isNaN(bi)) return ai-bi;
    return String(a.created_at||'').localeCompare(String(b.created_at||''));
  });
  items.forEach(function(m){
    const row=document.createElement('div'); row.className='msg'+(m.login===me?' me':'');
    const avatar=document.createElement('img'); avatar.className='avatar'; avatar.src=m.avatar||''; avatar.alt='';
    const bubble=document.createElement('div'); bubble.className='bubble'; bubble.textContent = String(m.text||'');
    const meta=document.createElement('div'); meta.className='meta';
    const dt = new Date(m.created_at || Date.now()).toLocaleString();
    meta.textContent = (m.login||'')+' · '+dt+(m.ref?(' · '+m.ref):'');
    const wrap=document.createElement('div'); wrap.appendChild(bubble); wrap.appendChild(meta);
    row.appendChild(avatar); row.appendChild(wrap);
    box.appendChild(row);
  });
  box.scrollTop = box.scrollHeight;
}

async function loadChat(sp, toastOnError){
  const r = await fetch('/admin/api/chat?since_id=' + __lastMsgId);
  const data = await r.json();
  if(!data || data.ok===false){ if(toastOnError) toast(data && data.error || 'Ошибка чата'); return; }
  const items = data.items || [];
  if(items.length){
    __lastMsgId = items[items.length-1].id;
    renderMessages(items);
  }
}

function toggleAutoChat(){ const ch=document.getElementById('autoChat'); if(!ch) return; if(__chatTimer){ clearInterval(__chatTimer); __chatTimer=null; } if(ch.checked){ __chatTimer=setInterval(()=>loadChat(false,false), 4000); } }
async function sendMsg(){
  const b=document.getElementById('btnSend'); if(b) b.disabled=true;
  try{
    const text=document.getElementById('ch_text').value.trim();
    const ref=document.getElementById('ch_ref').value.trim();
    if(!text){ toast('Введите сообщение'); return; }
    const me = ME.login || '';
    const box=document.getElementById('messages');
    const row=document.createElement('div'); row.className='msg me';
    const av=document.getElementById('header_avatar'); const avatar=document.createElement('img'); avatar.className='avatar'; avatar.src=(av && av.src)?av.src:''; row.appendChild(avatar);
    const bubble=document.createElement('div'); bubble.className='bubble sending'; bubble.textContent=text;
    const meta=document.createElement('div'); meta.className='meta'; meta.textContent=me+' · '+fmtTime(new Date().toISOString())+(ref?(' · '+ref):'');
    const wrap=document.createElement('div'); wrap.appendChild(bubble); wrap.appendChild(meta);
    row.appendChild(wrap); box.appendChild(row); box.scrollTop = box.scrollHeight;

    const r=await api('/api/chat',{method:'POST',body:JSON.stringify({text,ref})}, false);
    if(r.ok){
      bubble.classList.remove('sending');
      const tick=document.createElement('div'); tick.className='tick'; tick.textContent='✓✓'; bubble.appendChild(tick);
      document.getElementById('ch_text').value=''; document.getElementById('ch_ref').value='';
    } else {
      bubble.classList.remove('sending'); bubble.classList.add('failed');
      const tick=document.createElement('div'); tick.className='tick'; tick.textContent='×'; bubble.appendChild(tick);
      toast(r.error||'Ошибка отправки');
    }
  } finally{ if(b) b.disabled=false; }
}

document.addEventListener('keydown', function(e){
  if(e.key==='Enter' && !e.shiftKey && document.getElementById('ch_text')===document.activeElement){ e.preventDefault(); sendMsg(); }
});

// --- news ---
async function loadNews(sp){
  const box=document.getElementById('news'); if(sp){ box.innerHTML='<div class="pill">Загружаем…</div>'; }
  const r = await api('/api/news', {method:'GET'}, sp);
  if(!r || r.ok===false){ box.innerHTML='<div class="muted">Невозможно получить ленту</div>'; return; }
  const items = (r.items||[]).slice(0,5); if(!items.length){ box.innerHTML='<div class="muted">Нет новостей</div>'; } else { box.innerHTML=''; }
  items.forEach(function(p){
    const card=document.createElement('div'); card.className='news-card';
    const head=document.createElement('div'); head.className='news-head';
    const ava=document.createElement('img'); ava.className='news-ava'; ava.src=p.channel_image||''; head.appendChild(ava);
    const name=document.createElement('div'); name.innerHTML='<b>'+(p.channel_name||'SEABLUU')+'</b> <span class="muted" style="margin-left:6px">'+(p.date||'')+'</span>'; head.appendChild(name);
    card.appendChild(head);
    const text=document.createElement('div'); text.style.marginTop='6px'; text.textContent = p.text || ''; card.appendChild(text);
    if(p.image){ const img=document.createElement('img'); img.className='news-img'; img.src=p.image; card.appendChild(img); }
    box.appendChild(card);
  });
  // показать форму публикации только owner
  const form = document.getElementById('newsForm');
  if(form) form.style.display = (IS_OWNER ? 'block' : 'none');
}
async function publishNews(){
  const text = (document.getElementById('nw_text')||{}).value||'';
  const image = (document.getElementById('nw_image')||{}).value||'';
  if(!text.trim()){ toast('Введите текст'); return; }
  const r = await api('/api/news/publish',{method:'POST',body:JSON.stringify({text,image})}, true);
  toast(r.ok?'Опубликовано':(r.error||'Ошибка'));
  if(r.ok){ document.getElementById('nw_text').value=''; document.getElementById('nw_image').value=''; loadNews(false); }
}

// --- profile avatar load + header self-fill ---
async function uploadAvatarRaw(file, targetInputId, previewId, updateHeader){
  const r = await fetch('/admin/api/admins/upload_avatar?filename='+encodeURIComponent(file.name||'avatar.jpg'), { method:'POST', headers:{'Content-Type': file.type || 'application/octet-stream'}, body: file });
  let j=null; try{ j=await r.json(); }catch(e){ j={ok:false,error:'bad_json'}; }
  if(!j.ok){ toast(j.error||'Ошибка загрузки'); return; }
  const url = j.url;
  const input=document.getElementById(targetInputId); if(input) input.value=url;
  if(previewId){ const img=document.getElementById(previewId); if(img) img.src=url; }
  const hdr=document.getElementById('header_avatar'); if(updateHeader && hdr) hdr.src=url;
  toast('Аватар загружен');
}
async function saveMyAvatar(){
  const url=document.getElementById('me_avatar').value.trim();
  if(!url){ toast('Ссылка пуста'); return; }
  const r=await api('/api/admins/avatar',{method:'POST',body:JSON.stringify({avatar:url})}, true);
  if(r.ok){ toast('Сохранено'); const img=document.getElementById('me_preview'); if(img) img.src=url; const hdr=document.getElementById('header_avatar'); if(hdr) hdr.src=url; } else { toast(r.error||'Ошибка'); }
}
async function loadMeToHeader(){
  const r = await fetch('/admin/api/me');
  const me = await r.json();
  if(!me || me.ok===false) return;
  ME = me;
  const name = document.getElementById('me_login');   if(name) name.textContent = me.login || '';
  const hdr  = document.getElementById('header_avatar'); if(hdr) hdr.src = me.avatar || '';
  const prev = document.getElementById('me_preview'); if(prev) prev.src = me.avatar || '';
  const inp  = document.getElementById('me_avatar');  if(inp)  inp.value = me.avatar || '';
}


loadMeToHeader();
</script>
</html>
""".replace("__USER__", user_login)\
  .replace("__IS_OWNER__", _owner_js_flag(user_login))\
  .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))

# ------------------------ routes: pages ------------------------
@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> str:
    user = _authed_login(request)
    if not user:
        return _LOGIN_HTML
    return _admin_page_html(user)

# ------------------------ routes: auth ------------------------
@router.post("/api/login")
async def api_login(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", "")).strip()
    adm = _get_admin(login)
    if not adm or adm.get("password_hash") != _hash_pwd(login, password):
        return JSONResponse({"ok": False, "error": "Неверные логин или пароль"}, status_code=401)
    token = _make_token(login)
    r = JSONResponse({"ok": True})
    # На проде можно secure=True
    r.set_cookie("adm_session", token, max_age=12*3600, httponly=True, secure=False, samesite="lax", path="/admin")
    return r

@router.post("/api/logout")
async def api_logout() -> JSONResponse:
    r = JSONResponse({"ok": True})
    r.delete_cookie("adm_session", path="/admin")
    return r

@router.get("/api/me")
async def api_me(request: Request) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    adm = _get_admin(login) or {}
    return JSONResponse({"login": login, "role": adm.get("role", ""), "avatar": adm.get("avatar", "")})

# ------------------------ routes: orders & statuses ------------------------
@router.get("/api/search")
async def api_search(request: Request, q: str = Query("")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    q = (q or "").strip()
    cache_key = "recent50" if not q else f"q:{q.lower()}"
    cached = _cache_get(cache_key, ttl=6)
    if cached is not None:
        return JSONResponse({"items": cached})
    items: List[Dict[str, Any]] = []
    if not q:
        try:
            items = sheets.list_recent_orders(50)  # если функция поддерживает лимит
        except Exception:
            items = sheets.list_recent_orders()    # бэкап без лимита
    else:
        try:
            from .main import extract_order_id, _looks_like_username  # type: ignore
        except Exception:
            def extract_order_id(s: str) -> Optional[str]:
                s = (s or "").strip().upper()
                m = re.search(r"([A-Z]{2,3})[-\\s]?([0-9]{2,})", s)
                return f"{m.group(1)}-{m.group(2)}" if m else None
            def _looks_like_username(s: str) -> bool:
                return str(s or "").strip().startswith("@")
        oid = extract_order_id(q)
        if oid:
            o = sheets.get_order(oid)
            if o: items = [o]
        if not items and _looks_like_username(q):
            items = sheets.get_orders_by_username(q)
        if not items:
            items = sheets.get_orders_by_phone(q)
    _cache_set(cache_key, items)
    return JSONResponse({"items": items})

@router.post("/api/status")
async def api_set_status(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    order_id = str(payload.get("order_id", "")).strip()
    new_status = str(payload.get("status", "")).strip()
    pick_index = payload.get("pick_index")
    if not order_id:
        return JSONResponse({"ok": False, "error": "order_id is required"}, status_code=400)
    if (not new_status) and (pick_index is not None):
        try:
            i = int(pick_index)
            if 0 <= i < len(STATUSES):
                new_status = f"adm:pick_status_id:{i}"
        except Exception:
            pass
    if not new_status:
        return JSONResponse({"ok": False, "error": "status or pick_index is required"}, status_code=400)
    try:
        ok = sheets.update_order_status(order_id, new_status)
    except Exception:
        return JSONResponse({"ok": False, "error": "update_failed"}, status_code=500)
    _cache_clear()
    # уведомим подписчиков
    _notify_subscribers(order_id, new_status)
    return JSONResponse({"ok": True, "order_id": order_id, "status": new_status})

@router.post("/api/status/bulk")
async def api_set_status_bulk(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ids = list(payload.get("order_ids") or [])
    status = str(payload.get("status") or "")
    pick_index = payload.get("pick_index")
    if (not status) and (pick_index is not None):
        try:
            i = int(pick_index)
            if 0 <= i < len(STATUSES):
                status = f"adm:pick_status_id:{i}"
        except Exception:
            pass
    if not ids or not status:
        return JSONResponse({"ok": False, "error": "order_ids and status required"}, status_code=400)
    updated = 0
    for oid in ids:
        try:
            if sheets.update_order_status(str(oid), status):
                updated += 1
                _notify_subscribers(str(oid), status)
        except Exception:
            pass
    _cache_clear()
    return JSONResponse({"ok": True, "updated": updated})

@router.post("/api/orders")
async def api_create_order(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    order_id = str(payload.get("order_id", "")).strip().upper()
    origin = str(payload.get("origin", "")).strip().upper()
    status = str(payload.get("status", "")).strip()
    clients = str(payload.get("clients", "")).strip()
    note = str(payload.get("note", "")).strip()
    if not order_id or not origin or not status:
        return JSONResponse({"ok": False, "error": "order_id, origin, status required"}, status_code=400)
    try:
        ws = sheets.get_worksheet("orders")
        if not ws.get_all_values():
            ws.append_row(["order_id","origin","status","clients","note","created_at","updated_at"])
        ws.append_row([order_id, origin, status, clients, note, sheets._now(), sheets._now()])
    except Exception:
        return JSONResponse({"ok": False, "error": "create_failed"}, status_code=500)
    # подпишем клиентов (если сможем) и уведомим
    usernames = [u.strip() for u in re.split(r"[ ,;]+", clients) if u.strip()]
    for u in usernames:
        if not u.startswith("@"): continue
        try:
            uid = _resolve_username_to_uid(u)
            if uid:
                try:
                    # дадим знать и в подписки
                    if hasattr(sheets, "upsert_subscription"):
                        sheets.upsert_subscription(uid, order_id)  # type: ignore[attr-defined]
                    _send_tg(int(uid), f"Вам создан разбор {order_id}. Вы будете получать обновления статуса.")
                except Exception:
                    pass
        except Exception:
            pass
    _cache_clear()
    return JSONResponse({"ok": True, "order_id": order_id})

# ------------------------ routes: admins/media ------------------------
def _safe_filename(name: str) -> str:
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or f"avatar_{int(time.time())}.bin"
    return name

_MEDIA_DIR = os.path.join(os.getcwd(), "media", "avatars")

@router.get("/media/avatars/{filename}")
async def media_avatar(filename: str):
    fn = _safe_filename(filename)
    path = os.path.join(_MEDIA_DIR, fn)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path)

@router.post("/api/admins/upload_avatar")
async def api_upload_avatar_raw(request: Request, filename: str = Query(default="")) -> JSONResponse:
    me = _authed_login(request)
    if not me:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        raw = await request.body()
        if not raw:
            return JSONResponse({"ok": False, "error": "empty_body"}, status_code=400)
        fn = _safe_filename(filename or f"{me}_{int(time.time())}.jpg")
        os.makedirs(_MEDIA_DIR, exist_ok=True)
        path = os.path.join(_MEDIA_DIR, fn)
        with open(path, "wb") as f:
            f.write(raw)
        url = f"/admin/media/avatars/{fn}"
        return JSONResponse({"ok": True, "url": url})
    except Exception:
        return JSONResponse({"ok": False, "error": "upload_failed"}, status_code=500)

@router.get("/api/admins")
async def api_admins(request: Request) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        return JSONResponse({"ok": True, "items": _list_admins()})
    except Exception:
        return JSONResponse({"ok": False, "error": "admins_error"}, status_code=500)

@router.post("/api/admins")
async def api_add_admin(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    me = _authed_login(request)
    if not me:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", "")).strip()
    avatar = str(payload.get("avatar", "")).strip()
    if not login or not password:
        return JSONResponse({"ok": False, "error": "login_or_password_empty"}, status_code=400)
    ok = _add_admin(me, login, password, role="admin", avatar=avatar)
    return JSONResponse({"ok": bool(ok), **({} if ok else {"error": "forbidden_or_exists"})}, status_code=200 if ok else 403)

@router.post("/api/admins/avatar")
async def api_set_my_avatar(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    me = _authed_login(request)
    if not me:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    url = str(payload.get("avatar", "")).strip()
    if not url:
        return JSONResponse({"ok": False, "error": "empty_url"}, status_code=400)
    ok = _set_admin_avatar(me, url)
    return JSONResponse({"ok": bool(ok), **({} if ok else {"error": "update_failed"})}, status_code=200 if ok else 500)

# ------------------------ routes: clients & addresses ------------------------
@router.get("/api/clients")
async def api_clients(request: Request) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        ws = sheets.get_worksheet("clients")
        records = ws.get_all_records()
        items = []
        for r in records:
            username = r.get("username") or r.get("login") or r.get("tg") or ""
            phone = r.get("phone") or r.get("tel") or r.get("номер") or ""
            # подтянем заказы клиента
            orders = []
            try:
                if username:
                    orders = sheets.get_orders_by_username("@"+username.lstrip("@"))
            except Exception:
                orders = []
            items.append({"username": username, "name": r.get("name") or "", "phone": phone, "orders": orders})
        return JSONResponse({"ok": True, "items": items})
    except Exception:
        return JSONResponse({"ok": True, "items": []})

@router.get("/api/addresses")
async def api_addresses(request: Request, q: Optional[str] = Query(default="")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        ws = sheets.get_worksheet("addresses")
        records = ws.get_all_records()
        qq = (q or "").strip().lower()
        items = []
        for r in records:
            rec = {
                "username": r.get("username") or r.get("login") or r.get("tg") or "",
                "address": r.get("address") or r.get("адрес") or "",
                "phone": r.get("phone") or r.get("tel") or r.get("номер") or ""
            }
            if qq:
                hay = f"{rec['username']} {rec['address']} {rec['phone']}".lower()
                if qq not in hay: continue
            items.append(rec)
        return JSONResponse({"ok": True, "items": items})
    except Exception:
        return JSONResponse({"ok": True, "items": []})

# ------------------------ routes: chat ------------------------
@router.get("/api/chat")
async def api_chat_list(request: Request, since_id: int = Query(default=0)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    with _chat_lock:
        items = [m for m in _chat if int(m.get("id", 0)) > int(since_id)] if since_id else _chat[-200:]
    return JSONResponse({"ok": True, "items": items})

@router.post("/api/chat")
async def api_chat_send(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    me = _authed_login(request)
    if not me:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    text = str(payload.get("text", "")).strip()
    ref = str(payload.get("ref", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    adm = _get_admin(me) or {}
    msg = {
        "id": _chat_next_id(),
        "created_at": sheets._now(),  # твоя функция времени
        "login": me,
        "avatar": adm.get("avatar", ""),  # снапшот, чтобы все видели аву
        "text": text,
        "ref": ref,
    }
    _chat_append(msg)
    return JSONResponse({"ok": True, "id": msg["id"]})


# ------------------------ routes: news ------------------------
@router.get("/api/news")
async def api_news(request: Request) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        ws = sheets.get_worksheet("news")
        records = ws.get_all_records()
        items = []
        for r in records:
            txt = str(r.get("text") or "").strip()
            img = r.get("image") or r.get("photo") or ""
            if not txt and not img:  # пропускаем пустые строки
                continue
            items.append({
                "date": r.get("date") or r.get("created_at") or r.get("dt") or "",
                "text": txt,
                "image": img,
                "channel_image": r.get("channel_image") or r.get("avatar") or "",
                "channel_name": r.get("channel_name") or r.get("channel") or "SEABLUU"
            })
        return JSONResponse({"ok": True, "items": items})
    except Exception:
        return JSONResponse({"ok": True, "items": []})

@router.post("/api/news/publish")
async def api_news_publish(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    me = _authed_login(request)
    if not me:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if (_get_admin(me) or {}).get("role") != "owner":
        return JSONResponse({"ok": False, "error": "only_owner"}, status_code=403)
    text = str(payload.get("text","")).strip()
    image = str(payload.get("image","")).strip()
    if not text:
        return JSONResponse({"ok": False, "error": "text_required"}, status_code=400)
    try:
        ws = sheets.get_worksheet("news")
        if not ws.get_all_values():
            ws.append_row(["date","text","image","channel_image","channel_name"])
        ws.append_row([sheets._now(), text, image, "", "SEABLUU"])
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": False, "error": "publish_failed"}, status_code=500)

# ------------------------ export ------------------------
def get_admin_router() -> APIRouter:
    return router

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(get_admin_router(), prefix="/admin")
