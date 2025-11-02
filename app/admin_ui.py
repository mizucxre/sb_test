# -*- coding: utf-8 -*-
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

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


def _normalize_status(raw: str) -> str:
    if not raw:
        return "—"
    s = str(raw)
    if s.startswith("adm:pick_status_id") or "pick_status_id" in s:
        import re
        try:
            i = int(re.sub(r"[^0-9]", "", s))
            if 0 <= i < len(STATUSES):
                return STATUSES[i]
        except Exception:
            pass
    return s


def _secret() -> str:
    return os.getenv("ADMIN_SECRET", "dev-secret-change-me")


def _hash_pwd(login: str, password: str) -> str:
    data = f"{login}:{password}:{_secret()}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _admins_ws():
    ws = sheets.get_worksheet("admins")
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(["login", "password_hash", "role", "created_at"])  # role: owner/admin
    owner_login = os.getenv("ADMIN_LOGIN", "admin")
    owner_pass = os.getenv("ADMIN_PASSWORD", "admin")
    rows = ws.get_all_records()
    found_idx = None
    for i, r in enumerate(rows, start=2):
        if str(r.get("login", "")).strip().lower() == owner_login.strip().lower():
            found_idx = i
            break
    want_hash = _hash_pwd(owner_login, owner_pass)
    if found_idx is None:
        ws.append_row([owner_login, want_hash, "owner", sheets._now()])
    else:
        try:
            ws.update_cell(found_idx, 2, want_hash)
            ws.update_cell(found_idx, 3, "owner")
        except Exception:
            pass
    return ws


def _get_admin(login: str) -> Optional[Dict[str, Any]]:
    ws = _admins_ws()
    for r in ws.get_all_records():
        if str(r.get("login", "")).strip().lower() == str(login).strip().lower():
            return r
    return None


def _list_admins() -> List[Dict[str, Any]]:
    return _admins_ws().get_all_records()


def _add_admin(current_login: str, new_login: str, password: str, role: str = "admin") -> bool:
    cur = _get_admin(current_login)
    if not cur or cur.get("role") != "owner":
        return False
    new_login = str(new_login or "").strip()
    if not new_login or _get_admin(new_login):
        return False
    ws = _admins_ws()
    ws.append_row([new_login, _hash_pwd(new_login, password), role, sheets._now()])
    return True


def _sign(data: str) -> str:
    sig = hmac.new(_secret().encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def _make_token(login: str, ttl_seconds: int = 12 * 3600) -> str:
    exp = int(time.time()) + ttl_seconds
    payload = json.dumps({"login": login, "exp": exp}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    sig = _sign(b)
    return b + "." + sig


def _parse_token(token: str) -> Optional[str]:
    try:
        b, sig = token.split(".", 1)
        if not hmac.compare_digest(_sign(b), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return str(payload.get("login"))
    except Exception:
        return None


def _authed_login(request: Request) -> Optional[str]:
    token = request.cookies.get("adm_session", "")
    if not token:
        return None
    return _parse_token(token)


_LOGIN_HTML = """
<!doctype html>
<html lang=\"ru\">
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>SEABLUU — Вход</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--ink); display:grid; place-items:center; height:100vh; }
  .card { width:380px; padding:22px; border:1px solid var(--muted); border-radius:14px; background:var(--card); }
  input { width:100%; padding:12px 14px; border:1px solid var(--muted); border-radius:12px; background:#1c233b; color:var(--ink); }
  button { width:100%; padding:12px 16px; border-radius:12px; border:1px solid var(--muted); background:#24304d; color:var(--ink); cursor:pointer; }
  h1 { margin:0 0 14px 0; font-size:18px; }
  .gap { height:10px; }
  .err { color:#ff9aa2; font-size:13px; min-height:16px; }
</style>
<div class=\"card\">
  <h1>SEABLUU — Вход</h1>
  <div class=\"err\" id=\"err\"></div>
  <input id=\"login\" placeholder=\"Логин\" autocomplete=\"username\" />
  <div class=\"gap\"></div>
  <input id=\"pwd\" type=\"password\" placeholder=\"Пароль\" autocomplete=\"current-password\" />
  <div class=\"gap\"></div>
  <button onclick=\"doLogin()\">Войти</button>
</div>
<script>
async function doLogin(){
  const login=document.getElementById('login').value.trim();
  const password=document.getElementById('pwd').value;
  const r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login,password})});
  const j=await r.json();
  if(!j.ok){ document.getElementById('err').innerText=j.error||'Ошибка входа'; return; }
  location.reload();
}
</script>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request) -> str:
    user = _authed_login(request)
    if not user:
        return _LOGIN_HTML

    options = ''.join([f'<option value="adm:pick_status_id:{i}">{s}</option>' for i, s in enumerate(STATUSES)])
    html = """
<!doctype html>
<html lang=\"ru\">
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>SEABLUU — Админ‑панель</title>
<style>
  :root { --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--ink); }
  header { padding:14px 16px; border-bottom:1px solid var(--muted); position:sticky; top:0; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); backdrop-filter:saturate(150%) blur(6px); display:flex; justify-content:space-between; align-items:center; }
  h1 { margin:0; font-size:18px; }
  .wrap { max-width:1100px; margin:18px auto; padding:0 12px; }
  .tabs { display:flex; gap:8px; flex-wrap:wrap; }
  .tab { padding:8px 10px; border:1px solid var(--muted); background:#1c233b; border-radius:10px; cursor:pointer; }
  .active { background:#24304d; }
  .list { margin-top:16px; display:grid; gap:10px; }
  .item { padding:12px; border:1px solid var(--muted); border-radius:12px; background:var(--card); display:grid; grid-template-columns: 140px 1fr; gap:10px; align-items:center; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .search { display:flex; gap:8px; margin-top:10px; }
  input, select { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#1c233b; color:var(--ink); }
  button { padding:10px 12px; border:1px solid var(--muted); border-radius:10px; background:#2b3961; color:var(--ink); cursor:pointer; }
  .muted { color:#c7d2fe99; font-size:13px; }
  .toast { position:fixed; left:50%; bottom:18px; transform:translateX(-50%) translateY(20px); opacity:0; background:#1c233b; color:#e6ebff; border:1px solid var(--muted); padding:10px 14px; border-radius:12px; transition:all .35s ease; box-shadow:0 10px 20px rgba(0,0,0,.25); }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
</style>
<header>
  <h1>SEABLUU — Админ‑панель</h1>
  <div class=\"row\"><span class=\"muted\">__USER__</span> <button onclick=\"logout()\">Выйти</button></div>
</header>
<div class=\"wrap\">
  <div class=\"tabs\">
    <div class=\"tab active\" data-tab=\"orders\" onclick=\"openTab('orders')\">Заказы</div>
    <div class=\"tab\" data-tab=\"create\" onclick=\"openTab('create')\">Создать разбор</div>
    <div class=\"tab\" data-tab=\"clients\" onclick=\"openTab('clients')\">Клиенты</div>
    <div class=\"tab\" data-tab=\"addresses\" onclick=\"openTab('addresses')\">Адреса</div>
    <div class=\"tab\" data-tab=\"admins\" onclick=\"openTab('admins')\">Админы</div>
  </div>

  <div id=\"tab_orders\">
    <div class=\"search\">
      <input id=\"q\" placeholder=\"order_id / @username / телефон\" />
      <button onclick=\"runSearch()\">Искать</button>
    </div>
    <div id=\"list\" class=\"list\"></div>
  </div>

  <div id=\"tab_create\" hidden>
    <div class=\"row\" style=\"margin-top:10px\">
      <input id=\"c_order_id\" placeholder=\"order_id (например CN-12345)\" />
      <select id=\"c_origin\"> <option value=\"CN\">CN</option> <option value=\"KR\">KR</option> </select>
      <select id=\"c_status\"> __OPTIONS__ </select>
    </div>
    <div class=\"row\" style=\"margin-top:10px\">
      <input id=\"c_clients\" placeholder=\"клиенты через запятую (@user1, @user2)\" style=\"min-width:420px\" />
      <input id=\"c_note\" placeholder=\"заметка\" style=\"min-width:260px\" />
      <button onclick=\"createOrder()\">Создать</button>
    </div>
    <div id=\"c_msg\" class=\"muted\" style=\"margin-top:8px\"></div>
  </div>

  <div id=\"tab_clients\" hidden>
    <div class=\"search\"><input id=\"cq\" placeholder=\"поиск по имени/телефону/username\"/> <button onclick=\"loadClients()\">Найти</button></div>
    <div id=\"clients\" class=\"list\"></div>
  </div>

  <div id=\"tab_addresses\" hidden>
    <div class=\"search\"><input id=\"aq\" placeholder=\"username для фильтра (необязательно)\"/> <button onclick=\"loadAddresses()\">Загрузить</button></div>
    <div id=\"addresses\" class=\"list\"></div>
  </div>

  <div id=\"tab_admins\" hidden>
    <div class=\"search\">
      <input id=\"a_login\" placeholder=\"новый логин\" />
      <input id=\"a_pwd\" type=\"password\" placeholder=\"пароль\" />
      <button onclick=\"addAdmin()\">Добавить админа</button>
    </div>
    <div id=\"admins\" class=\"list\"></div>
  </div>
</div>
<div id=\"toast\" class=\"toast\"></div>
<script>
const STATUSES = __STATUSES__;
function openTab(name){
  for(const id of ['orders','create','clients','addresses','admins']){
    document.getElementById('tab_'+id).hidden = (id!==name);
    const el = document.querySelector('.tab[data-tab="'+id+'"]');
    if(el){ el.classList.toggle('active', id===name); }
  }
  if(name==='orders'){ runSearch(); }
  if(name==='clients'){ loadClients(); }
  if(name==='addresses'){ loadAddresses(); }
  if(name==='admins'){ loadAdmins(); }
}
async function api(path, opts={}){
  const r = await fetch('/admin'+path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
  return await r.json();
}
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'), 1800); }
function statusName(x){ if(!x) return '—'; if(x.includes('pick_status_id')){ const i=parseInt(x.replace(/[^0-9]/g,'')); if(!isNaN(i)&&i>=0&&i<STATUSES.length) return STATUSES[i]; } return x; }
async function runSearch(){
  const q = document.getElementById('q').value.trim();
  const data = await api('/api/search?q='+encodeURIComponent(q));
  const list = document.getElementById('list'); list.innerHTML='';
  for(const o of (data.items||[])){
    const div=document.createElement('div'); div.className='item'; const dt=(o.updated_at||'').replace('T',' ').slice(0,16);
    div.innerHTML=`<div class="oid">${o.order_id||''}</div><div>
      <div>Статус: <b>${statusName(o.status)}</b></div>
      <div class="muted">Страна: ${(o.origin||o.country||'—').toUpperCase()} · Обновлено: ${dt||'—'} · Клиент: ${o.client_name||'—'}</div>
      <div class="row" style="margin-top:8px">
        <select id="pick_${o.order_id}">${STATUSES.map((s,i)=>`<option value="${i}" ${statusName(o.status)===s?'selected':''}>${s}</option>`).join('')}</select>
        <button onclick="saveStatus('${o.order_id}')">Сохранить статус</button>
      </div></div>`; list.appendChild(div);
  }
}
async function saveStatus(oid){
  const sel=document.getElementById('pick_'+CSS.escape(oid));
  const pick_index=parseInt(sel.value);
  const res=await api('/api/status',{method:'POST',body:JSON.stringify({order_id:oid,pick_index})});
  if(res && res.ok!==false) toast('Статус сохранён'); else toast(res.error||'Ошибка сохранения');
  await runSearch();
}
async function createOrder(){
  const order_id=document.getElementById('c_order_id').value.trim();
  const origin=document.getElementById('c_origin').value;
  const status=document.getElementById('c_status').value;
  const clients=document.getElementById('c_clients').value.trim();
  const note=document.getElementById('c_note').value.trim();
  const r=await api('/api/orders',{method:'POST',body:JSON.stringify({order_id,origin,status,clients,note})});
  if(r.ok){ toast('Разбор создан'); } else { toast(r.error||'Ошибка'); }
}
async function loadClients(){
  const q=document.getElementById('cq').value.trim();
  const data=await api('/api/clients?q='+encodeURIComponent(q));
  const box=document.getElementById('clients'); box.innerHTML='';
  for(const c of (data.items||[])){
    const div=document.createElement('div'); div.className='item';
    div.innerHTML=`<div class="oid">${c.username||''}</div><div>
      <div>${c.full_name||'—'} — ${c.phone||'—'}</div>
      <div class="muted">${c.city||'—'}, ${c.address||'—'} (${c.postcode||'—'})</div>
    </div>`; box.appendChild(div);
  }
}
async function loadAddresses(){
  const q=document.getElementById('aq').value.trim();
  const data=await api('/api/addresses?q='+encodeURIComponent(q));
  const box=document.getElementById('addresses'); box.innerHTML='';
  for(const a of (data.items||[])){
    const div=document.createElement('div'); div.className='item';
    div.innerHTML=`<div class="oid">${a.username||a.user_id||''}</div><div>
      <div>${a.full_name||'—'} — ${a.phone||'—'}</div>
      <div class="muted">${a.city||'—'}, ${a.address||'—'} (${a.postcode||'—'})</div>
    </div>`; box.appendChild(div);
  }
}
async function loadAdmins(){
  const data=await api('/api/admins');
  const box=document.getElementById('admins'); box.innerHTML='';
  for(const a of (data.items||[])){
    const div=document.createElement('div'); div.className='item';
    div.innerHTML=`<div class="oid">${a.login}</div><div><div>Роль: <b>${a.role}</b></div><div class="muted">Создан: ${a.created_at||''}</div></div>`; box.appendChild(div);
  }
}
async function addAdmin(){
  const login=document.getElementById('a_login').value.trim();
  const password=document.getElementById('a_pwd').value;
  const r=await api('/api/admins',{method:'POST',body:JSON.stringify({login,password})});
  if(!r.ok){ toast(r.error||'Ошибка'); return; }
  document.getElementById('a_login').value=''; document.getElementById('a_pwd').value='';
  await loadAdmins();
}
async function logout(){ await api('/api/logout',{method:'POST'}); location.reload(); }
runSearch();
</script>
</html>
"""
    return (
        html
        .replace("__USER__", user)
        .replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))
        .replace("__OPTIONS__", options)
    )


@router.post("/api/login")
async def api_login(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", ""))
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
    items: List[Dict[str, Any]] = []
    if not q:
        items = sheets.list_recent_orders(30)
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
    ok = sheets.update_order_status(order_id, new_status)
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
    return JSONResponse({"ok": ok, "order_id": order_id, "status": new_status})


@router.get("/api/clients")
async def api_clients(request: Request, q: str = Query("")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    df = sheets.search_clients(q or None)
    items = [] if df.empty else df.sort_values(by="updated_at", ascending=False).head(100).to_dict(orient="records")
    return JSONResponse({"items": items})


@router.get("/api/addresses")
async def api_addresses(request: Request, q: str = Query("")) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    ws = sheets.get_worksheet("addresses")
    values = ws.get_all_records()
    if q:
        qn = q.strip().lstrip("@").lower()
        values = [r for r in values if str(r.get("username", "")).strip().lower() == qn]
    return JSONResponse({"items": values[-200:]})


@router.get("/api/admins")
async def api_admins(request: Request) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    cur = _get_admin(login)
    if not cur:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    items = _list_admins() if cur.get("role") == "owner" else [{"login": cur.get("login"), "role": cur.get("role"), "created_at": cur.get("created_at") }]
    return JSONResponse({"items": items})


@router.post("/api/admins")
async def api_admins_add(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    login = _authed_login(request)
    if not login:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    nl = str(payload.get("login", "")).strip()
    pw = str(payload.get("password", ""))
    ok = _add_admin(login, nl, pw, role="admin")
    if not ok:
        return JSONResponse({"ok": False, "error": "not_allowed_or_exists"}, status_code=403)
    return JSONResponse({"ok": True})


@router.post("/api/orders")
async def api_create_order(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not _authed_login(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    order_id = str(payload.get("order_id", "")).strip()
    origin = (payload.get("origin") or "").strip().upper()
    status = str(payload.get("status", "")).strip()
    clients = str(payload.get("clients", "")).strip()
    note = str(payload.get("note", "")).strip()
    if not order_id or origin not in ("CN", "KR"):
        return JSONResponse({"ok": False, "error": "order_id_and_origin_required"}, status_code=400)
    sheets.add_order({"order_id": order_id, "client_name": clients, "origin": origin, "status": status, "note": note})
    usernames: List[str] = []
    if clients:
        for tok in [t.strip() for t in clients.split(',') if t.strip()]:
            if tok.startswith("@"): tok = tok[1:]
            if tok: usernames.append(tok)
    if usernames:
        sheets.ensure_participants(order_id, usernames)
        sheets.ensure_clients_from_usernames(usernames)
    return JSONResponse({"ok": True, "order_id": order_id})


def get_admin_router() -> APIRouter:
    return router
