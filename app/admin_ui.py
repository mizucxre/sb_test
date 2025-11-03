
# -*- coding: utf-8 -*-
from __future__ import annotations
import base64, hashlib, hmac, json, os, time, urllib.parse, urllib.request, re
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from . import sheets

# ------------------ Статусы ------------------
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

# ------------------ Мини‑кэш ------------------
_CACHE: Dict[str, Any] = {}

def _cache_get(k: str, ttl: int = 10):
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

# ------------------ Утилиты ------------------

def _normalize_status(raw: str) -> str:
    if not raw:
      return "—"
    s = str(raw)
    if "pick_status_id" in s:
      try:
        i = int(re.sub(r"[^0-9]", "", s))
        if 0 <= i < len(STATUSES):
          return STATUSES[i]
      except Exception:
        pass
    return s

def _secret() -> str:
    return (os.getenv("ADMIN_SECRET", "dev-secret") or "dev-secret").strip()

def _hash_pwd(login: str, password: str) -> str:
    base = f"{login.strip().lower()}:{password}:{_secret()}".encode()
    return hashlib.sha256(base).hexdigest()

def _admins_ws():
    ws = sheets.get_worksheet("admins")
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(["login", "password_hash", "role", "avatar", "created_at"])
    # Обновить/создать владельца
    owner_login = (os.getenv("ADMIN_LOGIN", "admin") or "admin").strip()
    owner_pass = (os.getenv("ADMIN_PASSWORD", "admin") or "admin").strip()
    owner_avatar = os.getenv("ADMIN_AVATAR", "")
    rows = ws.get_all_records()
    want_hash = _hash_pwd(owner_login, owner_pass)
    found_rows = [i for i, r in enumerate(rows, start=2) if str(r.get("login", "")).strip().lower() == owner_login.lower()]
    if found_rows:
        i = found_rows[0]
        try:
            ws.update_cell(i, 2, want_hash)  # password_hash
            ws.update_cell(i, 3, "owner")    # role
            if owner_avatar:
                ws.update_cell(i, 4, owner_avatar)  # avatar
        except Exception:
            pass
        # удалить дубликаты
        for extra in found_rows[1:][::-1]:
            try:
                ws.delete_rows(extra)
            except Exception:
                pass
    else:
        ws.append_row([owner_login, want_hash, "owner", owner_avatar, sheets._now()])
    return ws

def _header_index(ws, name: str) -> Optional[int]:
    try:
        headers = ws.row_values(1)
        for idx, h in enumerate(headers, start=1):
            if h.strip().lower() == name.strip().lower():
                return idx
    except Exception:
        pass
    return None

def _admin_row_index(login: str) -> Optional[int]:
    ws = _admins_ws()
    try:
        rows = ws.get_all_records()
        for i, r in enumerate(rows, start=2):
            if str(r.get("login", "")).strip().lower() == login.strip().lower():
                return i
    except Exception:
        pass
    return None

def _get_admin(login: str) -> Optional[Dict[str, Any]]:
    ws = _admins_ws()
    for r in ws.get_all_records():
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
    ws = _admins_ws()
    # Пишем ровно по колонкам: login, password_hash, role, avatar, created_at
    ws.append_row([new_login, _hash_pwd(new_login, password), role, avatar, sheets._now()])
    return True

def _set_admin_avatar(target_login: str, avatar_url: str) -> bool:
    ws = _admins_ws()
    row = _admin_row_index(target_login)
    col = _header_index(ws, "avatar") or 4
    if not row:
        return False
    try:
        ws.update_cell(row, col, avatar_url)
        return True
    except Exception:
        return False

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
    token = request.cookies.get("adm_session", "")
    return _parse_token(token) if token else None

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
        chat_ids = []
        for s in subs:
            if str(s.get("order_id", "")) == order_id:
                try:
                    chat_ids.append(int(s.get("user_id")))
                except Exception:
                    pass
        if not chat_ids:
            return
        text = f"Статус {order_id}: {_normalize_status(new_status)}"
        for uid in set(chat_ids):
            try:
                params = urllib.parse.urlencode({"chat_id": uid, "text": text})
                urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage?{params}", timeout=5).read()
                sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception:
                pass
    except Exception:
        pass

# ------------------ HTML ------------------
_LOGIN_HTML = '''
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
'''

@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> str:
    user = _authed_login(request)
    if not user:
        return _LOGIN_HTML

    options = ''.join([f'<option value="adm:pick_status_id:{i}">{s}</option>' for i, s in enumerate(STATUSES)])
    html = '''
<!doctype html>
<html lang="ru">
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SEABLUU — Админ‑панель</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; --accent:#4f5fff; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--ink); }
  header { padding:14px 16px; border-bottom:1px solid var(--muted); position:sticky; top:0; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); backdrop-filter:saturate(150%) blur(6px); display:flex; justify-content:space-between; align-items:center; z-index:5; }
  h1 { margin:0; font-size:18px; }
  .wrap { max-width:1100px; margin:18px auto; padding:0 12px 70px; }
  .tabs { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin-bottom:12px; position:sticky; top:56px; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); padding:10px 0; z-index:4; }
  .tab { padding:8px 10px; border:1px solid var(--muted); background:#1c233b; border-radius:10px; text-decoration:none; color: var(--ink); }
  .tab.active { background:#24304d; }
  .list { margin-top:12px; display:grid; gap:10px; }
  .item { padding:12px; border:1px solid var(--muted); border-radius:12px; background:var(--card); display:grid; grid-template-columns: 160px 1fr; gap:10px; align-items:center; }
  .item.home{max-width:920px;margin:12px auto 0}
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .search { display:flex; gap:8px; margin-top:8px; }
  input, select, textarea { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#1c233b; color:#e6ebff; }
  textarea { width:100%; min-height:60px; }
  button { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#2b3961; color:#e6ebff; cursor:pointer; }
  .btn[disabled]{opacity:.6;cursor:not-allowed;filter:saturate(60%)}
  .muted { color:#c7d2fecc; font-size:13px; }
  .toast { position:fixed; left:50%; bottom:18px; transform:translateX(-50%) translateY(20px); opacity:0; background:#1c233b; color:#e6ebff; border:1px solid var(--muted); padding:10px 14px; border-radius:12px; transition:all .35s ease; box-shadow:0 10px 20px rgba(0,0,0,.25); z-index:100; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .overlay{position:fixed; left:50%; top:50%; transform:translate(-50%,-50%); display:none; align-items:center; justify-content:center; background:transparent; z-index:50}
  .overlay.show{display:flex}
  .spinner{background:#1c233b;border:1px solid var(--muted);color:#e6ebff;padding:10px 14px;border-radius:12px; box-shadow:0 8px 20px rgba(0,0,0,.35)}
  /* вкладки без JS */
  .section{display:none}
  .section:target{display:block}
  #tab_home{display:block}
  /* чат */
  .chat-wrap{max-width:920px;margin:0 auto;display:grid;gap:8px}
  .messages{height:60vh; min-height:360px; overflow:auto; display:flex; flex-direction:column; gap:6px; padding:6px}
  .msg{display:flex; gap:8px; align-items:flex-end; max-width:80%}
  .msg .bubble{background:#1e2a49; border:1px solid var(--muted); padding:8px 10px; border-radius:14px 14px 14px 4px; white-space:pre-wrap}
  .msg.me{margin-left:auto; flex-direction:row-reverse}
  .msg.me .bubble{background:#294172; border-color:#3b4f83; border-radius:14px 14px 4px 14px}
  .avatar{width:34px;height:34px;border-radius:50%;object-fit:cover;border:1px solid var(--muted); background:#0b1020}
  .meta{font-size:12px; color:#c7d2fe99; margin-top:2px}
  .composer{position:sticky; bottom:0; background:linear-gradient(0deg,rgba(11,16,32,1),rgba(11,16,32,.8)); padding-top:8px}
  .pill{padding:6px 10px;border:1px solid var(--muted);border-radius:999px;background:#1c233b;color:var(--ink);font-size:13px}
</style>
<header>
  <h1>SEABLUU — Админ‑панель</h1>
  <div class="row"><span class="muted">__USER__</span> <button onclick="logout()">Выйти</button></div>
</header>

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
    <div class="item home" style="grid-template-columns: 1fr;">
      <div style="font-weight:600">Новости магазина</div>
      <div class="row">
        <button class="btn" onclick="loadNews(true)">Обновить</button>
        <span class="muted">t.me/seabluushop (если недоступно — покажем подсказку)</span>
      </div>
      <div id="news" class="list"></div>
    </div>
  </div>

  <div id="tab_orders" class="section">
    <div class="search">
      <input id="q" placeholder="order_id / @username / телефон" />
      <button id="btnSearch" class="btn" onclick="loadOrders(true)">Обновить</button>
    </div>
    <div class="muted">Показываем до 20 записей.</div>
    <div id="orders" class="list"></div>
  </div>

  <div id="tab_create" class="section">
    <div class="row" style="margin-top:6px">
      <input id="c_order_id" placeholder="только цифры (например 12345)" inputmode="numeric" pattern="[0-9]*" oninput="this.value=this.value.replace(/\D+/g,'')" />
      <select id="c_origin"> <option value="CN">CN</option> <option value="KR">KR</option> </select>
      <select id="c_status"> __OPTIONS__ </select>
    </div>
    <div class="row" style="margin-top:6px">
      <input id="c_clients" placeholder="клиенты через запятую (@user1, @user2)" style="min-width:420px" />
      <input id="c_note" placeholder="заметка" style="min-width:260px" />
      <button id="btnCreate" class="btn" onclick="createOrder()">Создать</button>
    </div>
  </div>

  <div id="tab_clients" class="section">
    <div class="row"><button id="btnClients" class="btn" onclick="loadClients(true)">Обновить</button><span class="muted">До 20 записей</span></div>
    <div id="clients" class="list"></div>
  </div>

  <div id="tab_addresses" class="section">
    <div class="row">
      <input id="aq" placeholder="username для фильтра (опц.) — без @" style="min-width:240px"/>
      <button id="btnAddr" class="btn" onclick="loadAddresses(true)">Обновить</button>
      <span class="muted">До 20 записей</span>
    </div>
    <div id="addresses" class="list"></div>
  </div>

  <div id="tab_admins" class="section">
    <div class="item" style="grid-template-columns: 110px 1fr;">
      <div>Добавить админа</div>
      <div class="row" style="gap:6px">
        <input id="a_login" placeholder="новый логин" />
        <input id="a_pwd" type="password" placeholder="пароль" />
        <input id="a_avatar" placeholder="avatar URL (опц.)" style="min-width:320px" />
        <input id="a_file" type="file" accept="image/*" />
        <button id="btnUpload" class="btn" onclick="uploadAvatar('a_file','a_avatar')">Загрузить аву</button>
        <button id="btnAddAdmin" class="btn" onclick="addAdmin()">Добавить</button>
      </div>
    </div>
    <div class="item" style="grid-template-columns: 110px 1fr;">
      <div>Мой профиль</div>
      <div class="row" style="gap:6px">
        <img id="my_avatar_preview" class="avatar" src="" alt="avatar"/>
        <input id="me_avatar" placeholder="avatar URL" style="min-width:320px" />
        <input id="me_file" type="file" accept="image/*" />
        <button class="btn" onclick="uploadAvatar('me_file','me_avatar','my_avatar_preview')">Загрузить</button>
        <button class="btn" onclick="saveMyAvatar()">Сохранить</button>
        <button class="btn" onclick="loadAdmins(true)">Обновить список</button>
      </div>
    </div>
    <div id="admins" class="list"></div>
  </div>

  <div id="tab_chat" class="section">
    <div class="chat-wrap">
      <div class="row">
        <button class="btn" onclick="loadChat(true,true)">Обновить чат</button>
        <label class="row pill" style="gap:6px"><input id="autoChat" type="checkbox" onchange="toggleAutoChat()"> автообновление</label>
      </div>
      <div id="messages" class="messages"></div>
      <div class="composer row">
        <input id="ch_text" placeholder="Сообщение… (Enter — отправить)" style="flex:1" />
        <input id="ch_ref" placeholder="Привязка: @username или CN-12345" style="min-width:220px" />
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
})();

const STATUSES = __STATUSES__;
let __pending=0;
let __chatTimer=null;
let __lastCount=0;

function overlay(show){ const ov=document.getElementById('overlay'); if(!ov) return; ov.classList[show?'add':'remove']('show'); }
async function api(path, opts={}, showSpinner=false){
  if(showSpinner){ __pending++; if(__pending===1) overlay(true); }
  try{
    const r = await fetch('/admin'+path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
    let text = await r.text();
    let data;
    try{ data = JSON.parse(text); } catch(e){ data = {ok:false, error:'bad_json', status:r.status, raw:text.slice(0,200)}; }
    if(!r.ok){ data = Object.assign({ok:false}, data||{}); if(!data.error) data.error = 'HTTP '+r.status; }
    return data;
  } catch(e){
    return {ok:false, error: (e && e.message) || 'network_error'};
  } finally { if(showSpinner){ __pending--; if(__pending<=0) overlay(false); } }
}
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'), 2000); }
function statusName(x){ if(!x) return '—'; if(x.includes('pick_status_id')){ const i=parseInt(x.replace(/[^0-9]/g,'')); if(!isNaN(i)&&i>=0&&i<STATUSES.length) return STATUSES[i]; } return x; }
function fmtTime(s){ if(!s) return ''; const d=new Date(s); if(isNaN(+d)) return s; return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }

async function loadOrders(sp){ const q = (document.getElementById('q')||{value:''}).value.trim(); const data = await api('/api/search?q='+encodeURIComponent(q), {}, sp); const box=document.getElementById('orders'); box.innerHTML=''; if(!data||data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; } const arr=(data.items||[]).slice(0,20); if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; } for(const o of arr){ const div=document.createElement('div'); div.className='item'; const dt=(o.updated_at||'').replace('T',' ').slice(0,16); const opts=STATUSES.map((s,i)=>`<option value="\${i}" \${statusName(o.status)===s?'selected':''}>\${s}</option>`).join(''); div.innerHTML=`<div class="oid">\${o.order_id||''}</div><div><div>Статус: <b>\${statusName(o.status)}</b></div><div class="muted">Страна: \${(o.origin||o.country||'—').toUpperCase()} · Обновлено: \${dt||'—'} · Клиент: \${o.client_name||'—'}</div><div class="row" style="margin-top:6px"><select id="pick_\${o.order_id}">\${opts}</select><button class="btn" onclick="saveStatus('\${o.order_id}', this)">Сохранить статус</button></div></div>`; box.appendChild(div);} }
async function saveStatus(oid, btn){ if(btn) btn.disabled=true; try{ const sel=document.getElementById('pick_'+CSS.escape(oid)); const pick_index=parseInt(sel.value); const res=await api('/api/status',{method:'POST',body:JSON.stringify({order_id:oid,pick_index})}, true); toast(res && res.ok!==false?'Статус сохранён':(res.error||'Ошибка')); } finally{ if(btn) btn.disabled=false; } }
async function createOrder(){ const b=document.getElementById('btnCreate'); if(b) b.disabled=true; try{ const origin=document.getElementById('c_origin').value; const idnum=(document.getElementById('c_order_id').value.trim()).replace(/\D+/g,''); if(!idnum){ toast('Введите цифры номера заказа'); return; } const order_id=origin+'-'+idnum; const status=document.getElementById('c_status').value; const clients=document.getElementById('c_clients').value.trim(); const note=document.getElementById('c_note').value.trim(); const r=await api('/api/orders',{method:'POST',body:JSON.stringify({order_id,origin,status,clients,note})}, true); toast(r.ok?'Разбор создан':(r.error||'Ошибка')); } finally{ if(b) b.disabled=false; } }

async function loadClients(sp){ const data=await api('/api/clients',{method:'GET'}, sp); const box=document.getElementById('clients'); box.innerHTML=''; if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; } const arr=(data.items||[]).slice(0,20); if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; } for(const u of arr){ const div=document.createElement('div'); div.className='item'; div.style.gridTemplateColumns='200px 1fr'; div.innerHTML='<div>'+ (u.username?('@'+u.username):(u.name||'')) +'</div><div class="muted">'+(u.phone||'')+'</div>'; box.appendChild(div);} }

async function loadAddresses(sp){ const q=(document.getElementById('aq')||{value:''}).value.trim(); const data=await api('/api/addresses?q='+encodeURIComponent(q), {method:'GET'}, sp); const box=document.getElementById('addresses'); box.innerHTML=''; if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; } const arr=(data.items||[]).slice(-20); if(!arr.length){ box.innerHTML='<div class="muted">Пусто</div>'; return; } for(const a of arr){ const div=document.createElement('div'); div.className='item'; div.style.gridTemplateColumns='200px 1fr'; div.innerHTML='<div>'+ (a.username?('@'+a.username):'—') +'</div><div class="muted">'+(a.address||'')+'</div>'; box.appendChild(div);} }

async function loadAdmins(sp){ const data=await api('/api/admins',{method:'GET'}, sp); const box=document.getElementById('admins'); box.innerHTML=''; if(!data || data.ok===false){ box.innerHTML='<div class="muted">Ошибка: '+(data&&data.error||'нет данных')+'</div>'; return; } const arr=data.items||[]; for(const a of arr){ const div=document.createElement('div'); div.className='item'; div.style.gridTemplateColumns='80px 1fr'; const av=a.avatar?'<img class="avatar" src="'+a.avatar+'">':'<div class="avatar" style="display:grid;place-items:center;font-size:12px">—</div>'; div.innerHTML= av + '<div><div><b>'+a.login+'</b> <span class="muted">('+a.role+')</span></div><div class="muted">'+(a.created_at||'')+'</div></div>'; box.appendChild(div);} const me='__USER__'; const self=(arr||[]).find(x=>x.login===me); if(self){ const img=document.getElementById('my_avatar_preview'); if(img) img.src=self.avatar||''; const inp=document.getElementById('me_avatar'); if(inp) inp.value=self.avatar||''; } }

async function uploadAvatar(fileInputId, targetInputId, previewId){ const fileEl=document.getElementById(fileInputId); if(!fileEl || !fileEl.files || !fileEl.files[0]){ toast('Выберите файл'); return; } const f = fileEl.files[0]; const r = await fetch('/admin/api/admins/upload_avatar?filename='+encodeURIComponent(f.name||'avatar.jpg'), { method:'POST', headers:{'Content-Type': f.type || 'application/octet-stream'}, body: f }); let j=null; try{ j=await r.json(); }catch(e){ j={ok:false,error:'bad_json'}; } if(!j.ok){ toast(j.error||'Ошибка загрузки'); return; } const url = j.url; document.getElementById(targetInputId).value = url; if(previewId){ const img=document.getElementById(previewId); if(img) img.src=url; } toast('Аватар загружен'); }
async function saveMyAvatar(){ const url=document.getElementById('me_avatar').value.trim(); if(!url){ toast('Ссылка пуста'); return; } const r=await api('/api/admins/avatar',{method:'POST',body:JSON.stringify({avatar:url})}, true); if(r.ok){ toast('Сохранено'); } else { toast(r.error||'Ошибка'); } }
async function addAdmin(){ const login=document.getElementById('a_login').value.trim(); const password=document.getElementById('a_pwd').value; const avatar=document.getElementById('a_avatar').value.trim(); if(!login || !password){ toast('Введите логин и пароль'); return; } const r=await api('/api/admins',{method:'POST',body:JSON.stringify({login,password,avatar})}, true); if(!r.ok){ toast(r.error||'Ошибка'); return; } document.getElementById('a_login').value=''; document.getElementById('a_pwd').value=''; document.getElementById('a_avatar').value=''; document.getElementById('a_file').value=''; toast('Админ добавлен'); }

function renderMessages(items){ const box=document.getElementById('messages'); box.innerHTML=''; const me='__USER__'; items.forEach(m=>{ const row=document.createElement('div'); row.className='msg'+(m.login===me?' me':''); const av = m.avatar?('<img class="avatar" src="'+m.avatar+'">'):'<div class="avatar" />'; const meta = '<div class="meta">'+(m.login||'')+' · '+fmtTime(m.created_at||'')+ (m.ref?(' · '+m.ref):'') +'</div>'; const bubble = '<div><div class="bubble">'+(m.text||'')+'</div>'+meta+'</div>'; row.innerHTML = av + bubble; box.appendChild(row); }); box.scrollTop = box.scrollHeight; }
async function loadChat(sp, toastOnError){ const data=await api('/api/chat',{method:'GET'}, sp); if(!data || data.ok===false){ if(toastOnError) toast(data && data.error || 'Ошибка чата'); return; } __lastCount = (data.items||[]).length; renderMessages(data.items||[]); }
function toggleAutoChat(){ const ch=document.getElementById('autoChat'); if(!ch) return; if(__chatTimer){ clearInterval(__chatTimer); __chatTimer=null; } if(ch.checked){ __chatTimer=setInterval(()=>loadChat(false,false), 4000); } }
async function sendMsg(){ const b=document.getElementById('btnSend'); if(b) b.disabled=true; try{ const text=document.getElementById('ch_text').value.trim(); const ref=document.getElementById('ch_ref').value.trim(); if(!text){ toast('Введите сообщение'); return; } const r=await api('/api/chat',{method:'POST',body:JSON.stringify({text,ref})}, true); if(r.ok){ document.getElementById('ch_text').value=''; document.getElementById('ch_ref').value=''; await loadChat(false,false); } else { toast(r.error||'Ошибка'); } } finally{ if(b) b.disabled=false; } }
document.addEventListener('keydown', function(e){ if(e.key==='Enter' && !e.shiftKey && document.getElementById('ch_text')===document.activeElement){ e.preventDefault(); sendMsg(); } });

// Новости
async function loadNews(sp){ const box=document.getElementById('news'); if(sp){ box.innerHTML='<div class="pill">Загружаем…</div>'; } const r = await api('/api/news', {method:'GET'}, sp); if(!r || r.ok===false || !r.items || !r.items.length){ box.innerHTML='<div class="muted">Невозможно получить ленту (требуется интернет с сервера). Откройте канал: t.me/seabluushop</div>'; return; } box.innerHTML=''; r.items.slice(0,5).forEach(p=>{ const div=document.createElement('div'); div.className='item'; div.style.gridTemplateColumns='1fr'; div.innerHTML='<div style="white-space:pre-wrap">'+p.text+'</div><div class="muted">'+(p.date||'')+'</div>'; box.appendChild(div); }); }
window.onerror=function(msg){ try{ const t=document.getElementById('toast'); if(t){ t.textContent='Ошибка: '+msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),3000);} }catch(e){} };
</script>
</html>
'''
    return (html
        .replace("__USER__", user)
        .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))
        .replace("__OPTIONS__", options)
    )

# ------------------ API ------------------
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
    """Заказы: если q пустой — последние 20."""
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
            if o:
                items = [o]
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
    except Exception as e:
        return JSONResponse({"ok": False, "error": "update_failed"}, status_code=500)
    try:
        subs = sheets.get_all_subscriptions()
        for s in subs:
            if str(s.get("order_id","")) == order_id:
                try:
                    sheets.set_last_sent_status(int(s.get("user_id")), order_id, "")
                except Exception:
                    pass
    except Exception:
        pass
    _notify_subscribers(order_id, new_status)
    _cache_clear()
    return JSONResponse({"ok": ok, "order_id": order_id, "status": new_status})

@router.post("/api/orders")
async def api_create_order(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    order_id = str(payload.get("order_id", "")).strip()
    origin   = (str(payload.get("origin", "")).strip() or "").upper()[:2]
    status   = str(payload.get("status", "")).strip()
    note     = str(payload.get("note", "")).strip()
    clients_raw = str(payload.get("clients", "")).strip()
    if not order_id or not origin:
        return JSONResponse({"ok": False, "error": "order_id and origin are required"}, status_code=400)
    if not (order_id.startswith("CN-") or order_id.startswith("KR-")):
        order_id = f"{origin}-{order_id.lstrip(' -')}"
    try:
        sheets.add_order({"order_id": order_id, "origin": origin, "status": status or "", "note": note})
        usernames = [u.strip() for u in clients_raw.split(",") if u.strip()]
        created = sheets.ensure_clients_from_usernames(usernames)
        if usernames:
            sheets.ensure_participants(order_id, usernames)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "create_failed"}, status_code=500)
    _cache_clear()
    return JSONResponse({"ok": True, "order_id": order_id})

@router.get("/api/clients")
async def api_clients(request: Request) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    try:
        df = sheets.search_clients(None)
        items = [] if df.empty else df.sort_values(by="updated_at", ascending=False).head(20).to_dict(orient="records")
    except Exception:
        items = []
    return JSONResponse({"items": items})

@router.get("/api/addresses")
async def api_addresses(request: Request, q: str = Query("")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ws = sheets.get_worksheet("addresses")
    try:
        values = ws.get_all_records()
    except Exception:
        values = []
    if q:
        qn = q.strip().lstrip("@").lower()
        values = [r for r in values if str(r.get("username", "")).strip().lower() == qn]
    return JSONResponse({"items": values[-20:]})

@router.get("/api/admins")
async def api_admins(request: Request) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    cur = _get_admin(login)
    if not cur:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    items = _list_admins() if cur.get("role") == "owner" else [{"login": cur.get("login"), "role": cur.get("role"), "avatar": cur.get("avatar"), "created_at": cur.get("created_at") }]
    return JSONResponse({"items": items})

@router.post("/api/admins")
async def api_admins_add(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    nl = str(payload.get("login", "")).strip()
    pw = str(payload.get("password", ""))
    avatar = str(payload.get("avatar", "")).strip()
    ok = _add_admin(login, nl, pw, role="admin", avatar=avatar)
    if not ok:
        return JSONResponse({"ok": False, "error": "not_allowed_or_exists"}, status_code=403)
    return JSONResponse({"ok": True})

@router.post("/api/admins/avatar")
async def api_admins_avatar(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    target = str(payload.get("login", "")).strip() or login
    cur = _get_admin(login) or {}
    if target != login and cur.get("role") != "owner":
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    avatar = str(payload.get("avatar", "")).strip()
    if not avatar:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    ok = _set_admin_avatar(target, avatar)
    return JSONResponse({"ok": ok})

# ---- Загрузка и раздача аватаров (без multipart) ----
_MEDIA_DIR = os.getenv("ADMIN_MEDIA_DIR", os.path.join(os.getcwd(), "data", "avatars"))
os.makedirs(_MEDIA_DIR, exist_ok=True)

@router.post("/api/admins/upload_avatar")
async def upload_avatar(request: Request, filename: str = Query("avatar.jpg")) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    content = await request.body()
    if not content:
        return JSONResponse({"ok": False, "error": "empty_body"}, status_code=400)
    name, ext = os.path.splitext(filename or "avatar.jpg")
    ext = (ext or "").lower()
    if ext not in [".png",".jpg",".jpeg",".gif",".webp"]:
        ext = ".jpg"
    safe = "".join([c for c in login if c.isalnum() or c in "-_"]).strip("-_") or "user"
    fname = f"{safe}_{int(time.time())}{ext}"
    path = os.path.join(_MEDIA_DIR, fname)
    with open(path, "wb") as f:
        f.write(content)
    url = f"/admin/media/avatars/{fname}"
    return JSONResponse({"ok": True, "url": url})

@router.get("/media/avatars/{name}")
async def get_avatar(name: str):
    path = os.path.join(_MEDIA_DIR, name)
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return FileResponse(path)

# ------------------ Чат ------------------
def _chat_ws():
    ws = sheets.get_worksheet("chat")
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(["id", "login", "text", "ref", "created_at"])
    return ws

@router.get("/api/chat")
async def api_chat_list(request: Request) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ws = _chat_ws()
    try:
        rows = ws.get_all_records()
    except Exception:
        rows = []
    rows = rows[-120:]
    admin_map = {a.get("login"): a for a in _list_admins()}
    for r in rows:
        adm = admin_map.get(r.get("login")) or {}
        r["avatar"] = adm.get("avatar", "")
    return JSONResponse({"ok": True, "items": rows})

@router.post("/api/chat")
async def api_chat_post(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    text = str(payload.get("text", "")).strip()
    ref = str(payload.get("ref", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    ws = _chat_ws()
    try:
        ws.append_row([str(int(time.time()*1000)), login, text, ref, sheets._now()])
    except Exception:
        return JSONResponse({"ok": False, "error": "write_failed"}, status_code=500)
    return JSONResponse({"ok": True})

# ------------------ Новости из Telegram ------------------
@router.get("/api/news")
async def api_news() -> JSONResponse:
    url = "https://t.me/s/seabluushop"
    try:
        html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8", "ignore")
        # очень грубый парсер: последние 5 текстовых блоков
        items = []
        for m in re.finditer(r'<div class="tgme_widget_message.*?>(.*?)</div>\s*</div>\s*</div>', html, re.S):
            block = m.group(1)
            # вытащим дату
            dt = ""
            md = re.search(r'datetime="([^"]+)"', block)
            if md:
                dt = md.group(1).replace("T"," ").replace("+00:00","")
            # текст
            text = re.sub(r'<[^>]+>', '', block)
            text = re.sub(r'\s+',' ', text).strip()
            if text:
                items.append({"text": text, "date": dt})
            if len(items) >= 8:
                break
        return JSONResponse({"ok": True, "items": items})
    except Exception:
        return JSONResponse({"ok": True, "items": []})

def get_admin_router() -> APIRouter:
    return router

