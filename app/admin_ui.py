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


_LOGIN_HTML = r"""
<!doctype html>
<html lang="ru">
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SEABLUU — Вход</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--ink); display:grid; place-items:center; height:100vh; }
  .card { width:420px; padding:22px; border:1px solid var(--muted); border-radius:14px; background:var(--card); box-shadow:0 10px 30px rgba(0,0,0,.25); }
  input { display:block; width:100%; padding:12px 14px; border:1px solid var(--muted); border-radius:12px; background:#dfe7f5; color:#0b1020; }
  button { display:block; width:100%; padding:12px 16px; border-radius:12px; border:1px solid var(--muted); background:#24304d; color:#e6ebff; cursor:pointer; }
  h1 { margin:0 0 14px 0; font-size:18px; }
  .gap { height:10px; }
  .err { color:#ff9aa2; font-size:13px; min-height:16px; }
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
    const j=await r.json();
    if(!j.ok){ document.getElementById('err').innerText=j.error||'Ошибка входа'; return; }
    location.reload();
  } finally { if(btn){ btn.disabled=false; btn.textContent=old; } }
}
window.onerror = function(msg){ try{ var el=document.getElementById('err'); if(el) el.innerText='Ошибка: '+msg; }catch(e){} };
</script>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> str:
    user = _authed_login(request)
    if not user:
        return _LOGIN_HTML

    options = ''.join([f'<option value="adm:pick_status_id:{i}">{s}</option>' for i, s in enumerate(STATUSES)])

    html = r"""
<!doctype html>
<html lang="ru">
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SEABLUU — Админ‑панель</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; --accent:#4f5fff; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--ink); }
  header { padding:10px 12px; border-bottom:1px solid var(--muted); position:sticky; top:0; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); backdrop-filter:saturate(150%) blur(6px); display:flex; justify-content:space-between; align-items:center; z-index:5; }
  h1 { margin:0; font-size:18px; }
  .wrap { max-width:1100px; margin:18px auto; padding:0 12px 70px; }
  .tabs { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin-bottom:12px; position:sticky; top:56px; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); padding:10px 0; z-index:4; }
  .tab { padding:8px 10px; border:1px solid var(--muted); background:#1c233b; border-radius:10px; text-decoration:none; color: var(--ink); }
  .tab.active { background:#24304d; }
  .list { margin-top:12px; display:grid; gap:10px; }
  .item { padding:12px; border:1px solid var(--muted); border-radius:12px; background:var(--card); display:grid; grid-template-columns: 160px 1fr; gap:10px; align-items:center; }
  .item.home{max-width:920px;margin:12px auto 0; grid-template-columns:1fr}
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .search { display:flex; gap:8px; margin-top:8px; }
  input, select, textarea { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#1c233b; color:#e6ebff; }
  textarea { width:100%; min-height:60px; }
  button { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#2b3961; color:#e6ebff; cursor:pointer; }
  .btn[disabled]{opacity:.7;cursor:wait}
  .muted { color:#c7d2fecc; font-size:13px; }
  .toast { position:fixed; left:50%; bottom:18px; transform:translateX(-50%) translateY(20px); opacity:0; background:#1c233b; color:#e6ebff; border:1px solid var(--muted); padding:10px 14px; border-radius:12px; transition:all .35s ease; box-shadow:0 10px 20px rgba(0,0,0,.25); z-index:100; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .overlay{position:fixed; left:50%; top:50%; transform:translate(-50%,-50%); display:none; align-items:center; justify-content:center; background:transparent; z-index:50}
  .overlay.show{display:flex}
  .spinner{background:#1c233b;border:1px solid var(--muted);color:#e6ebff;padding:10px 14px;border-radius:12px; box-shadow:0 8px 20px rgba(0,0,0,.35)}
  .section{display:none}
  .section:target{display:block}
  #tab_home{display:block}
  .chat-wrap{max-width:920px;margin:0 auto;display:grid;gap:8px}
  .messages{height:60vh; min-height:360px; overflow:auto; display:flex; flex-direction:column; gap:8px; padding:6px}
  .msg{display:flex; gap:8px; align-items:flex-end; max-width:80%}
  .msg .bubble{background:#1e2a49; border:1px solid var(--muted); padding:8px 10px; border-radius:14px 14px 14px 4px; white-space:pre-wrap; position:relative}
  .msg.me{margin-left:auto; flex-direction:row-reverse}
  .msg.me .bubble{background:#294172; border-color:#3b4f83; border-radius:14px 14px 4px 14px}
  .avatar{width:34px;height:34px;border-radius:50%;object-fit:cover;border:1px solid var(--muted); background:#0b1020}
  .avatar.sm{width:28px;height:28px;border-radius:50%}
  .avatar.lg{width:46px;height:46px}
  .meta{font-size:12px; color:#c7d2fe99; margin-top:2px}
  .composer{position:sticky; bottom:0; background:linear-gradient(0deg,rgba(11,16,32,1),rgba(11,16,32,.8)); padding-top:8px}
  .pill{padding:6px 10px;border:1px solid var(--muted);border-radius:999px;background:#1c233b;color:var(--ink);font-size:13px}
  .tick{position:absolute; right:6px; bottom:-16px; font-size:12px; color:#9fb0ff99}
  .sending::after{content:'⏳'; position:absolute; right:6px; bottom:-16px; font-size:12px; opacity:.9}
  .failed{border-color:#ff7b7b!important}
  .news-card{padding:12px;border:1px solid var(--muted); border-radius:12px; background:var(--card)}
  .news-head{display:flex;gap:10px;align-items:center}
  .news-ava{width:40px;height:40px;border-radius:50%;object-fit:cover;border:1px solid var(--muted); background:#111}
  .news-img{width:100%; max-height:440px; object-fit:cover; border-radius:10px;border:1px solid var(--muted); background:#111; margin-top:8px}
  .gear{width:28px;height:28px;cursor:pointer;opacity:.9}
  .drawer{position:fixed;top:0;right:-420px;width:380px;height:100vh;background:var(--card);border-left:1px solid var(--muted);box-shadow:-12px 0 24px rgba(0,0,0,.25);transition:right .28s ease;z-index:60;padding:16px}
  .drawer.show{right:0}
  .drop{border:1px dashed var(--muted);border-radius:12px;padding:16px;text-align:center;background:#1b233b;cursor:pointer}
  .drop.drag{background:#222c4a}
  .admin-card{display:grid;grid-template-columns:52px 1fr;gap:12px;align-items:center;padding:10px;border:1px solid var(--muted);border-radius:12px;background:#1b233b}
  .role{font-size:12px;border:1px solid var(--muted);padding:2px 6px;border-radius:999px;margin-left:6px;opacity:.9}
</style>
<header>
  <h1>SEABLUU — Админ‑панель</h1>
  <div class="row">
    <img id="header_avatar" class="avatar sm" src="" alt=""/>
    <span class="muted">__USER__</span>
    <svg class="gear" id="btnSettings" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path stroke-width="2" d="M12 8a4 4 0 100 8 4 4 0 000-8zm8.94 4a7.94 7.94 0 00-.16-1.64l2.02-1.57-2-3.46-2.42.98a8.05 8.05 0 00-2.84-1.64l-.43-2.56H9.89l-.43 2.56a8.05 8.05 0 00-2.84 1.64l-2.42-.98-2 3.46 2.02 1.57A7.94 7.94 0 003.06 12c0 .56.06 1.11.16 1.64l-2.02 1.57 2 3.46 2.42-.98a8.05 8.05 0 002.84 1.64l.43 2.56h4.26l.43-2.56a8.05 8.05 0 002.84-1.64l2.42.98 2-3.46-2.02-1.57c.1-.53.16-1.08.16-1.64z"/></svg>
    <button onclick="logout()">Выйти</button>
  </div>
</header>

<div id="drawer" class="drawer">
  <h3 style="margin:4px 0 12px 0">Настройки профиля</h3>
  <div class="news-card" style="display:grid;grid-template-columns:58px 1fr;gap:10px;align-items:center">
    <img id="me_preview" class="avatar lg" src="" alt=""/>
    <div>
      <div class="row" style="gap:6px">
        <input id="me_avatar" placeholder="avatar URL" style="min-width:260px" autocomplete="off"/>
        <button class="btn" onclick="saveMyAvatar()">Сохранить</button>
      </div>
      <div id="drop" class="drop" style="margin-top:8px">Перетащите файл сюда или нажмите, чтобы выбрать</div>
      <input id="me_file" type="file" accept="image/*" style="display:none"/>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="tabs">
    <a class="tab active" href="#tab_home">Главная</a>
    <a class="tab" href="#tab_orders">Заказы</a>
    <a class="tab" href="#tab_create">Создать разбор</a>
    <a class="tab" href="#tab_clients">Клиенты</a>
    <a class="tab" href="#tab_addresses">Адреса</a>
    <a class="tab" href="#tab_admins">Админы</a>
    <a class="tab" href="#tab_chat">Чат</a>
  </div>

  <div id="tab_home" class="section">
    <div class="item home">
      <div class="row"><button class="btn" onclick="loadNews(true)">Обновить новости</button><span class="muted">Покажем 5 последних постов</span></div>
      <div id="news" class="list"></div>
    </div>
  </div>

  <div id="tab_orders" class="section">
    <div class="search">
      <input id="q" placeholder="order_id / @username / телефон" autocomplete="off" autocapitalize="off" spellcheck="false"/>
      <button id="btnSearch" class="btn" onclick="loadOrders(true)">Обновить</button>
    </div>
    <div class="muted">До 20 записей</div>
    <div id="orders" class="list"></div>
  </div>

  <div id="tab_create" class="section">
    <div class="row" style="margin-top:6px">
      <input id="c_order_id" placeholder="только цифры (например 12345)" inputmode="numeric" autocomplete="off" autocapitalize="off" spellcheck="false" pattern="[0-9]*" oninput="this.value=this.value.replace(/\D+/g,'')" />
      <select id="c_origin"> <option value="CN">CN</option> <option value="KR">KR</option> </select>
      <select id="c_status"> __OPTIONS__ </select>
    </div>
    <div class="row" style="margin-top:6px">
      <input id="c_clients" placeholder="клиенты через запятую (@user1, @user2)" style="min-width:420px" autocomplete="off" autocapitalize="off" spellcheck="false"/>
      <input id="c_note" placeholder="заметка" style="min-width:260px" autocomplete="off" autocapitalize="off" spellcheck="false"/>
      <button id="btnCreate" class="btn" onclick="createOrder()">Создать</button>
    </div>
  </div>

  <div id="tab_clients" class="section">
    <div class="row"><button id="btnClients" class="btn" onclick="loadClients(true)">Обновить</button><span class="muted">До 20 записей</span></div>
    <div id="clients" class="list"></div>
  </div>

  <div id="tab_addresses" class="section">
    <div class="row">
      <input id="aq" placeholder="username для фильтра (опц.) — без @" style="min-width:240px" autocomplete="off" autocapitalize="off" spellcheck="false"/>
      <button id="btnAddr" class="btn" onclick="loadAddresses(true)">Обновить</button>
      <span class="muted">До 20 записей</span>
    </div>
    <div id="addresses" class="list"></div>
  </div>

  <div id="tab_admins" class="section">
    <div class="row"><button class="btn" onclick="loadAdmins(true)">Обновить список</button></div>
    <div id="admins" class="list"></div>
    <div style="height:8px"></div>
    <div class="news-card">
      <div style="font-weight:600;margin-bottom:6px">Добавить админа</div>
      <div class="row" style="gap:6px">
        <input id="a_login" placeholder="новый логин" autocomplete="off" autocapitalize="off" spellcheck="false"/>
        <input id="a_pwd" type="password" placeholder="пароль" autocomplete="new-password"/>
        <input id="a_avatar" placeholder="avatar URL (опц.)" style="min-width:320px" autocomplete="off"/>
        <input id="a_file" type="file" accept="image/*" style="display:none"/>
        <button class="btn" onclick="document.getElementById('a_file').click()">Загрузить аву</button>
        <button id="btnAddAdmin" class="btn" onclick="addAdmin()">Добавить</button>
      </div>
      <div id="a_drop" class="drop" style="margin-top:8px">Перетащите файл сюда или нажмите «Загрузить аву»</div>
    </div>
  </div>

  <div id="tab_chat" class="section">
    <div class="chat-wrap">
      <div class="row">
        <button class="btn" onclick="loadChat(true,true)">Обновить чат</button>
        <label class="row pill" style="gap:6px"><input id="autoChat" type="checkbox" onchange="toggleAutoChat()"> автообновление</label>
      </div>
      <div id="messages" class="messages"></div>
      <div class="composer row">
        <input id="ch_text" placeholder="Сообщение… (Enter — отправить)" style="flex:1" autocomplete="off" autocapitalize="off" spellcheck="false"/>
        <input id="ch_ref" placeholder="Привязка: @username или CN-12345" style="min-width:220px" autocomplete="off"/>
        <button id="btnSend" onclick="sendMsg()">Отправить</button>
      </div>
    </div>
  </div>
</div>
<div id="overlay" class="overlay"><div class="spinner" id="spinner">Загрузка…</div></div>
<div id="toast" class="toast"></div>

<script>
(function(){
  function setActive(){
    var id = location.hash || '#tab_home';
    try{
      var secs=document.querySelectorAll('.section');
      for(var i=0;i<secs.length;i++){ secs[i].style.display='none'; }
      var el=document.querySelector(id); if(el){ el.style.display='block'; }
      var tabs=document.querySelectorAll('.tabs .tab');
      for(var j=0;j<tabs.length;j++){ tabs[j].classList.toggle('active', tabs[j].getAttribute('href')===id); }
    }catch(e){ console.error(e); }
  }
  window.addEventListener('hashchange', setActive);
  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', setActive); else setActive();
  window.logout = function(){ fetch('/admin/api/logout',{method:'POST'}).then(function(){ location.reload(); }); };
  var dr=document.getElementById('drawer'), gear=document.getElementById('btnSettings');
  if(gear) gear.onclick=function(){ dr.classList.toggle('show'); };
  var drop=document.getElementById('drop'), file=document.getElementById('me_file');
  if(drop){
    drop.onclick=function(){ file.click(); };
    drop.ondragover=function(e){ e.preventDefault(); drop.classList.add('drag'); };
    drop.ondragleave=function(){ drop.classList.remove('drag'); };
    drop.ondrop=function(e){ e.preventDefault(); drop.classList.remove('drag'); if(e.dataTransfer.files && e.dataTransfer.files[0]) uploadAvatarRaw(e.dataTransfer.files[0],'me_avatar','me_preview',true); };
  }
  if(file){ file.onchange=function(){ if(file.files && file.files[0]) uploadAvatarRaw(file.files[0],'me_avatar','me_preview',true); }; }
  var adrop=document.getElementById('a_drop'), afile=document.getElementById('a_file');
  if(adrop){ adrop.onclick=function(){ afile.click(); }; adrop.ondragover=function(e){ e.preventDefault(); adrop.classList.add('drag'); }; adrop.ondragleave=function(){ adrop.classList.remove('drag'); }; adrop.ondrop=function(e){ e.preventDefault(); adrop.classList.remove('drag'); if(e.dataTransfer.files && e.dataTransfer.files[0]) uploadAvatarRaw(e.dataTransfer.files[0],'a_avatar',null,false); }; }
  if(afile){ afile.onchange=function(){ if(afile.files && afile.files[0]) uploadAvatarRaw(afile.files[0],'a_avatar',null,false); }; }
})();
const STATUSES = __STATUSES__;
let __pending=0; let __chatTimer=null;
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
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'), 2000); }
function statusName(x){ if(!x) return '—'; if(x.includes('pick_status_id')){ const i=parseInt(x.replace(/[^0-9]/g,'')); if(!isNaN(i)&&i>=0&&i<STATUSES.length) return STATUSES[i]; } return x; }
function fmtTime(s){ if(!s) return ''; const d=new Date(s); if(isNaN(+d)) return s; return d.toLocaleString(); }

function renderOrders(items){
  const list = document.getElementById('orders'); list.innerHTML='';
  if(!items.length){ list.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const o of items){
    const div=document.createElement('div'); div.className='item';
    const dt=(o.updated_at||'').replace('T',' ').slice(0,16);
    const opts = STATUSES.map((s,i)=>`<option value="${i}" ${statusName(o.status)===s?'selected':''}>${s}</option>`).join('');
    div.innerHTML=`<div class="oid">${o.order_id||''}</div>
      <div>
        <div>Статус: <b>${statusName(o.status)}</b></div>
        <div class="muted">Страна: ${(o.origin||o.country||'—').toUpperCase()} · Обновлено: ${dt||'—'} · Клиент: ${o.client_name||'—'}</div>
        <div class="row" style="margin-top:8px">
          <select id="pick_${o.order_id}">${opts}</select>
          <button class="btn" onclick="saveStatus('${o.order_id}', this)">Сохранить статус</button>
        </div>
      </div>`;
    list.appendChild(div);
  }
}
async function loadOrders(sp){
  const q = (document.getElementById('q')||{value:''}).value.trim();
  const data = await api('/api/search?q='+encodeURIComponent(q), {}, sp);
  if(!data || data.ok===false){ document.getElementById('orders').innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  renderOrders((data.items||[]).slice(0,20));
}
async function saveStatus(oid, btn){
  if(btn) btn.disabled=true; try{
    const sel=document.getElementById('pick_'+CSS.escape(oid));
    const pick_index=parseInt(sel.value);
    const res=await api('/api/status',{method:'POST',body:JSON.stringify({order_id:oid,pick_index})}, true);
    toast(res && res.ok!==false?'Статус сохранён':(res.error||'Ошибка'));
  } finally { if(btn) btn.disabled=false; }
}

async function createOrder(){
  const b=document.getElementById('btnCreate'); if(b) b.disabled=true; try{
    const origin=document.getElementById('c_origin').value;
    const idnum=(document.getElementById('c_order_id').value.trim()).replace(/\D+/g,'');
    if(!idnum){ toast('Введите цифры номера заказа'); return; }
    const order_id=origin+'-'+idnum;
    const status=document.getElementById('c_status').value;
    const clients=document.getElementById('c_clients').value.trim();
    const note=document.getElementById('c_note').value.trim();
    const r=await api('/api/orders',{method:'POST',body:JSON.stringify({order_id,origin,status,clients,note})}, true);
    toast(r.ok?'Разбор создан':(r.error||'Ошибка'));
  } finally{ if(b) b.disabled=false; }
}

async function loadClients(sp){
  const data=await api('/api/clients',{method:'GET'}, sp);
  const box=document.getElementById('clients'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=(data.items||[]).slice(0,20);
  if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const u of arr){
    const div=document.createElement('div'); div.className='item';
    div.style.gridTemplateColumns='160px 1fr';
    div.innerHTML=`<div>${u.username||u.name||''}</div><div class="muted">${u.phone||''}</div>`;
    box.appendChild(div);
  }
}

async function loadAddresses(sp){
  const q=(document.getElementById('aq')||{value:''}).value.trim();
  const data=await api('/api/addresses?q='+encodeURIComponent(q), {method:'GET'}, sp);
  const box=document.getElementById('addresses'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=(data.items||[]).slice(-20);
  if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const a of arr){
    const div=document.createElement('div'); div.className='item';
    div.style.gridTemplateColumns='220px 1fr';
    div.innerHTML=`<div>${a.username?('@'+a.username):'—'}</div><div class="muted">${a.address||''}</div>`;
    box.appendChild(div);
  }
}

async function loadAdmins(sp){
  const data=await api('/api/admins',{method:'GET'}, sp);
  const box=document.getElementById('admins'); box.innerHTML='';
  if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; }
  const arr=data.items||[]; if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; }
  for(const a of arr){
    const card=document.createElement('div'); card.className='admin-card';
    const img=document.createElement('img'); img.className='avatar'; img.src=a.avatar||''; img.alt='';
    const right=document.createElement('div');
    const title=document.createElement('div'); title.innerHTML='<b>'+a.login+'</b><span class="role">'+(a.role||'')+'</span>';
    const sub=document.createElement('div'); sub.className='muted'; sub.textContent=a.created_at||'';
    right.appendChild(title); right.appendChild(sub);
    card.appendChild(img); card.appendChild(right);
    box.appendChild(card);
  }
}
async function addAdmin(){
  const login=(document.getElementById('a_login')||{}).value||'';
  const password=(document.getElementById('a_pwd')||{}).value||'';
  const avatar=(document.getElementById('a_avatar')||{}).value||'';
  const r=await api('/api/admins',{method:'POST',body:JSON.stringify({login,password,avatar})}, true);
  toast(r.ok?'Добавлено':(r.error||'Ошибка'));
  if(r.ok){ loadAdmins(false); }
}
async function uploadAvatarRaw(file, targetInputId, previewId, updateHeader){
  const r = await fetch('/admin/api/admins/upload_avatar?filename='+encodeURIComponent(file.name||'avatar.jpg'), { method:'POST', headers:{'Content-Type': file.type || 'application/octet-stream'}, body: file });
  let j=null; try{ j=await r.json(); }catch(e){ j={ok:false,error:'bad_json'}; }
  if(!j.ok){ toast(j.error||'Ошибка загрузки'); return; }
  const url = j.url;
  const input=document.getElementById(targetInputId); if(input) input.value=url;
  if(previewId){ const img=document.getElementById(previewId); if(img) img.src=url; }
  if(updateHeader){ const hdr=document.getElementById('header_avatar'); if(hdr) hdr.src=url; }
  toast('Аватар загружен');
}
async function saveMyAvatar(){
  const url=document.getElementById('me_avatar').value.trim();
  if(!url){ toast('Ссылка пуста'); return; }
  const r=await api('/api/admins/avatar',{method:'POST',body:JSON.stringify({avatar:url})}, true);
  if(r.ok){ toast('Сохранено'); const img=document.getElementById('me_preview'); if(img) img.src=url; const hdr=document.getElementById('header_avatar'); if(hdr) hdr.src=url; } else { toast(r.error||'Ошибка'); }
}
async function loadMeToHeader(){
  const me='__USER__';
  const r=await api('/api/admins',{method:'GET'}, false);
  if(!r || !r.items) return;
  const self=(r.items.find(x=>x.login===me)) || r.items[0];
  if(self){
    const img=document.getElementById('me_preview'); if(img) img.src=self.avatar||'';
    const inp=document.getElementById('me_avatar'); if(inp) inp.value=self.avatar||'';
    const hdr=document.getElementById('header_avatar'); if(hdr) hdr.src=self.avatar||'';
  }
}

function renderMessages(items){
  const box=document.getElementById('messages'); box.innerHTML='';
  const me='__USER__';
  items.sort(function(a,b){
    const ai=parseInt(String(a.id||'0')); const bi=parseInt(String(b.id||'0'));
    if(!isNaN(ai) && !isNaN(bi)) return ai-bi;
    return String(a.created_at||'').localeCompare(String(b.created_at||''));
  });
  items.forEach(function(m){
    const row=document.createElement('div'); row.className='msg'+(m.login===me?' me':'');
    const avatar=document.createElement('img'); avatar.className='avatar'; avatar.src=m.avatar||''; avatar.alt='';
    const bubble=document.createElement('div'); bubble.className='bubble'; bubble.textContent = String(m.text||'');
    const meta=document.createElement('div'); meta.className='meta'; meta.textContent=(m.login||'')+' · '+fmtTime(m.created_at||'')+(m.ref?(' · '+m.ref):'');
    const wrap=document.createElement('div'); wrap.appendChild(bubble); wrap.appendChild(meta);
    row.appendChild(avatar); row.appendChild(wrap);
    box.appendChild(row);
  });
  box.scrollTop = box.scrollHeight;
}
async function loadChat(sp, toastOnError){
  const data=await api('/api/chat',{method:'GET'}, sp);
  if(!data || data.ok===false){ if(toastOnError) toast(data && data.error || 'Ошибка чата'); return; }
  renderMessages(data.items||[]);
}
function toggleAutoChat(){ const ch=document.getElementById('autoChat'); if(!ch) return; if(__chatTimer){ clearInterval(__chatTimer); __chatTimer=null; } if(ch.checked){ __chatTimer=setInterval(()=>loadChat(false,false), 4000); } }
async function sendMsg(){
  const b=document.getElementById('btnSend'); if(b) b.disabled=true;
  try{
    const text=document.getElementById('ch_text').value.trim();
    const ref=document.getElementById('ch_ref').value.trim();
    if(!text){ toast('Введите сообщение'); return; }
    const me='__USER__';
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
document.addEventListener('keydown', function(e){ if(e.key==='Enter' && !e.shiftKey && document.getElementById('ch_text')===document.activeElement){ e.preventDefault(); sendMsg(); } });

async function loadNews(sp){
  const box=document.getElementById('news'); if(sp){ box.innerHTML='<div class="pill">Загружаем…</div>'; }
  const r = await api('/api/news', {method:'GET'}, sp);
  if(!r || r.ok===false){ box.innerHTML='<div class="muted">Невозможно получить ленту. Откройте канал: t.me/seabluushop</div>'; return; }
  const items = (r.items||[]).slice(0,5); if(!items.length){ box.innerHTML='<div class="muted">Нет новостей</div>'; return; }
  box.innerHTML='';
  items.forEach(function(p){
    const card=document.createElement('div'); card.className='news-card';
    const head=document.createElement('div'); head.className='news-head';
    const ava=document.createElement('img'); ava.className='news-ava'; ava.src=p.channel_image||''; head.appendChild(ava);
    const name=document.createElement('div'); name.innerHTML='<b>SEABLUU</b> <span class="muted" style="margin-left:6px">'+(p.date||'')+'</span>'; head.appendChild(name);
    card.appendChild(head);
    const text=document.createElement('div'); text.style.marginTop='6px'; text.textContent = p.text || ''; card.appendChild(text);
    if(p.image){ const img=document.createElement('img'); img.className='news-img'; img.src=p.image; card.appendChild(img); }
    box.appendChild(card);
  });
}

loadMeToHeader();
</script>
</html>
"""
    return (
        html.replace("__USER__", user)
            .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))
            .replace("__OPTIONS__", options)
    )


@router.post("/api/login")
async def api_login(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", "")).strip()
    adm = _get_admin(login)
    if not adm or adm.get("password_hash") != _hash_pwd(login, password):
        return JSONResponse({"ok": False, "error": "Неверные логин или пароль"}, status_code=401)
    token = _make_token(login)
    r = JSONResponse({"ok": True})
    r.set_cookie("adm_session", token, max_age=12*3600, httponly=True, secure=False, samesite="lax", path="/admin")
    return r

@router.post("/api/logout")
async def api_logout() -> JSONResponse:
    r = JSONResponse({"ok": True})
    r.delete_cookie("adm_session", path="/admin")
    return r

@router.get("/api/search")
async def api_search(request: Request, q: str = Query("")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    q = (q or "").strip()
    cache_key = "recent20" if not q else f"q:{q.lower()}"
    cached = _cache_get(cache_key, ttl=6)
    if cached is not None:
        return JSONResponse({"items": cached})
    items: List[Dict[str, Any]] = []
    if not q:
        items = sheets.list_recent_orders(20)
    else:
        try:
            from .main import extract_order_id, _looks_like_username  # type: ignore
        except Exception:
            def extract_order_id(s: str) -> Optional[str]:
                s = (s or "").strip()
                return s if s and not s.startswith("@") else None
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
    pick_index = payload.get("pick_index")
    new_status = str(payload.get("status", "")).strip()
    if not order_id:
        return JSONResponse({"ok": False, "error": "order_id is required"}, status_code=400)
    if pick_index is not None:
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
    try:
        subs = sheets.get_all_subscriptions()
        for s in subs:
            if str(s.get("order_id","")) == order_id:
                try: sheets.set_last_sent_status(int(s.get("user_id")), order_id, "")
                except Exception: pass
    except Exception:
        pass
    _notify_subscribers(order_id, new_status)
    _cache_clear()
    return JSONResponse({"ok": True, "order_id": order_id, "status": new_status})

@router.post("/api/orders")
async def api_create_order(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    order_id = str(payload.get("order_id", ""))
