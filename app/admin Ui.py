# -*- coding: utf-8 -*-
from typing import List, Dict, Any
from fastapi import APIRouter, Body, Query
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


@router.get("/", response_class=HTMLResponse)
async def admin_page() -> str:
    return f"""
<!doctype html>
<html lang=\"ru\">
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>SEABLUU — Админ‑панель</title>
<style>
  :root {{ --bg:#0b1020; --card:#151b2d; --ink:#e6ebff; --muted:#9fb0ff3a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.45 system-ui, -apple-system, Segoe UI, Roboto, Arial; background:var(--bg); color:var(--ink); }}
  header {{ padding:14px 16px; border-bottom:1px solid var(--muted); position:sticky; top:0; background:linear-gradient(180deg,rgba(11,16,32,.95),rgba(11,16,32,.85)); backdrop-filter:saturate(150%) blur(6px); }}
  h1 {{ margin:0; font-size:18px; }}
  .wrap {{ max-width:980px; margin:18px auto; padding:0 12px; }}
  .search {{ display:flex; gap:8px; }}
  .search input {{ flex:1; padding:12px 14px; border:1px solid var(--muted); border-radius:12px; background:var(--card); color:var(--ink); }}
  .search button {{ padding:12px 16px; border-radius:12px; border:1px solid var(--muted); background:#24304d; color:var(--ink); cursor:pointer; }}
  .list {{ margin-top:16px; display:grid; gap:10px; }}
  .item {{ padding:12px; border:1px solid var(--muted); border-radius:12px; background:var(--card); display:grid; grid-template-columns: 140px 1fr; gap:10px; align-items:center; }}
  .oid {{ font-weight:600; letter-spacing:.2px; }}
  .status {{ opacity:.95; }}
  .meta {{ color:#c7d2fe99; font-size:13px; }}
  .row {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  select, .inline {{ padding:8px 10px; border-radius:10px; border:1px solid var(--muted); background:#1c233b; color:var(--ink); }}
  .save {{ padding:9px 12px; border-radius:10px; border:1px solid var(--muted); background:#2b3961; color:var(--ink); cursor:pointer; }}
  .empty {{ margin-top:24px; color:#c7d2fe80; }}
</style>
<header><h1>SEABLUU — Админ‑панель</h1></header>
<div class=\"wrap\">
  <div class=\"search\">
    <input id=\"q\" placeholder=\"order_id / @username / телефон\" />
    <button onclick=\"runSearch()\">Искать</button>
  </div>
  <div id=\"list\" class=\"list\"></div>
  <div id=\"empty\" class=\"empty\" hidden>Ничего не найдено.</div>
</div>
<script>
const STATUSES = {STATUSES!r};
async function api(path, opts={{}}) {{
  const r = await fetch('/admin' + path, Object.assign({{headers:{{'Content-Type':'application/json'}}}}, opts));
  return await r.json();
}}
function statusName(x) {{
  if (!x) return '—';
  if (x.includes('pick_status_id')) {{
    const i = parseInt(x.replace(/[^0-9]/g,''));
    if (!isNaN(i) && i>=0 && i<STATUSES.length) return STATUSES[i];
  }}
  return x;
}}
async function runSearch() {{
  const q = document.querySelector('#q').value.trim();
  const data = await api('/api/search?q=' + encodeURIComponent(q));
  const list = document.querySelector('#list');
  const empty = document.querySelector('#empty');
  list.innerHTML = '';
  if (!data.items || !data.items.length) {{ empty.hidden = false; return; }}
  empty.hidden = true;
  for (const o of data.items) {{
    const div = document.createElement('div');
    div.className = 'item';
    const dt = (o.updated_at || '').replace('T',' ').slice(0,16);
    div.innerHTML = `
      <div class="oid">${{o.order_id || ''}}</div>
      <div>
        <div class="status">Статус: <b>${{statusName(o.status)}} </b></div>
        <div class="meta">Страна: ${{(o.origin || o.country || '—').toUpperCase()}} · Обновлено: ${{dt || '—'}} · Клиент: ${{o.client_name || '—'}}</div>
        <div class="row" style="margin-top:8px">
          <select class="inline" id="pick_${{o.order_id}}">
            ${{STATUSES.map((s,i)=>`<option value="${{i}}" ${{statusName(o.status)===s?'selected':''}}>${{s}}</option>`).join('')}}
          </select>
          <button class="save" onclick="saveStatus('${{o.order_id}}')">Сохранить статус</button>
        </div>
      </div>`;
    list.appendChild(div);
  }}
}}
async function saveStatus(oid) {{
  const sel = document.querySelector('#pick_' + CSS.escape(oid));
  const pick_index = parseInt(sel.value);
  await api('/api/status', {{ method:'POST', body: JSON.stringify({{order_id: oid, pick_index}}) }});
  await runSearch();
}}
runSearch();
</script>
</html>
"""


@router.get("/api/search")
async def api_search(q: str = Query("")) -> JSONResponse:
    q = (q or "").strip()
    items: List[Dict[str, Any]] = []
    if not q:
        items = sheets.list_recent_orders(30)
    else:
        from .main import extract_order_id, _looks_like_username
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
async def api_set_status(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
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
    return JSONResponse({"ok": ok, "order_id": order_id, "status": new_status})


def get_admin_router() -> APIRouter:
    return router
