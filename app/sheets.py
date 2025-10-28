# -*- coding: utf-8 -*-
# app/sheets.py
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ========== Auth & helpers ==========
def _authorize():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    elif creds_file:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    else:
        raise RuntimeError("Missing GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE")
    return gspread.authorize(creds)

def _open_sheet():
    sid = os.getenv("GOOGLE_SHEETS_ID")
    if not sid:
        raise RuntimeError("GOOGLE_SHEETS_ID is not set")
    return _authorize().open_by_key(sid)

def _ensure_ws(title: str):
    sh = _open_sheet()
    try:
        ws = sh.worksheet(title)
        return ws
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=26)
        # headers
        if title == "orders":
            ws.append_row(["order_id","client_name","phone","origin","status","note","country","updated_at"])
        elif title == "participants":
            ws.append_row(["order_id","username","paid","qty","created_at","updated_at"])
        elif title == "subscriptions":
            ws.append_row(["user_id","order_id","last_sent_status","created_at","updated_at"])
        elif title == "addresses":
            ws.append_row(["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"])
        elif title == "clients":
            ws.append_row(["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"])
        elif title == "order_history":
            ws.append_row(["order_id","old_status","new_status","admin_username","ts"])
        return ws

def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()

def _normalize_username(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()

def _digits_only(s: str) -> str:
    import re
    return re.sub(r"\D+", "", str(s or ""))

# ========== Migration from addresses -> clients (one-time, soft) ==========
def _migrate_addresses_to_clients_if_needed():
    ws_clients = _ensure_ws("clients")
    # if clients already has data (beyond header), do nothing
    if len(ws_clients.get_all_values()) > 1:
        return

    # if addresses doesn't exist or empty, nothing to migrate
    sh = _open_sheet()
    try:
        ws_addr = sh.worksheet("addresses")
    except gspread.WorksheetNotFound:
        return

    rows = ws_addr.get_all_records()
    if not rows:
        return

    # choose latest by updated_at per username
    best: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        uname = _normalize_username(r.get("username"))
        if not uname:
            continue
        ts = r.get("updated_at") or r.get("created_at") or ""
        prev = best.get(uname)
        if prev is None or str(ts) > str(prev.get("updated_at") or prev.get("created_at") or ""):
            best[uname] = r

    # append to clients
    for uname, r in best.items():
        ws_clients.append_row([
            str(r.get("user_id") or ""),
            r.get("username") or uname,
            r.get("full_name") or "",
            r.get("phone") or "",
            r.get("city") or "",
            r.get("address") or "",
            r.get("postcode") or "",
            r.get("created_at") or now_iso(),
            r.get("updated_at") or now_iso(),
        ])

# ========== Orders ==========
def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    ws = _ensure_ws("orders")
    for r in ws.get_all_records():
        if str(r.get("order_id","")).strip().upper() == str(order_id).strip().upper():
            return r
    return None

def list_orders() -> List[Dict[str, Any]]:
    ws = _ensure_ws("orders")
    return ws.get_all_records()

# ========== Participants ==========
def list_participants(order_id: str) -> List[Dict[str, Any]]:
    ws = _ensure_ws("participants")
    order_id_norm = str(order_id or "").strip().upper()
    return [r for r in ws.get_all_records() if str(r.get("order_id","")).strip().upper() == order_id_norm]

def get_orders_by_username(username: str) -> List[Dict[str, Any]]:
    uname = _normalize_username(username)
    if not uname:
        return []
    parts = _ensure_ws("participants").get_all_records()
    order_ids = []
    seen = set()
    for r in parts:
        if _normalize_username(r.get("username")) == uname:
            oid = str(r.get("order_id") or "").strip()
            if oid and oid not in seen:
                order_ids.append(oid); seen.add(oid)
    if not order_ids:
        return []
    index = {o.get("order_id"): o for o in list_orders()}
    return [index[oid] for oid in order_ids if oid in index]

# ========== Clients ==========
def _get_clients_df() -> pd.DataFrame:
    _migrate_addresses_to_clients_if_needed()
    ws = _ensure_ws("clients")
    values = ws.get_all_records()
    df = pd.DataFrame(values)
    if df.empty:
        df = pd.DataFrame(columns=["user_id","username","full_name","phone","city","address","postcode","created_at","updated_at"])
    # normalize username
    if "username" in df.columns:
        df["username_norm"] = df["username"].astype(str).map(_normalize_username)
    else:
        df["username_norm"] = ""
    # normalize phone digits
    if "phone" in df.columns:
        df["phone_digits"] = df["phone"].astype(str).map(_digits_only)
    else:
        df["phone_digits"] = ""
    return df

def get_client_by_username(username: str) -> Optional[Dict[str, Any]]:
    df = _get_clients_df()
    uname = _normalize_username(username)
    if uname and not df.empty:
        sub = df[df["username_norm"] == uname]
        if not sub.empty:
            rec = sub.iloc[-1].to_dict()
            # strip helper columns
            for k in ["username_norm","phone_digits"]:
                if k in rec: rec.pop(k, None)
            return rec
    return None

def upsert_client_profile(user_id: Optional[int], username: str, full_name: str, phone: str, city: str, address: str, postcode: str):
    """Insert or update client by username (preferred) or by user_id."""
    _migrate_addresses_to_clients_if_needed()
    ws = _ensure_ws("clients")
    header = ws.row_values(1)
    rows = ws.get_all_records()
    uname = _normalize_username(username)

    # find row index (1-based) by username
    row_index = None
    for i, r in enumerate(rows, start=2):  # data starts at row 2
        if _normalize_username(r.get("username")) == uname:
            row_index = i

    now = now_iso()
    rec = {
        "user_id": str(user_id or ""),
        "username": username if username.startswith("@") else f"@{username}" if username else "",
        "full_name": full_name or "",
        "phone": phone or "",
        "city": city or "",
        "address": address or "",
        "postcode": postcode or "",
        "created_at": now,
        "updated_at": now,
    }

    # if exists -> update; else -> append
    def row_from_header(hdr: List[str], d: Dict[str, Any]) -> List[str]:
        return [str(d.get(c, "")) for c in hdr]

    if row_index:
        # keep original created_at if present
        ca = ws.cell(row_index, header.index("created_at")+1).value if "created_at" in header else ""
        if ca:
            rec["created_at"] = ca
        # write values
        ws.update(f"A{row_index}:{chr(64+len(header))}{row_index}", [row_from_header(header, rec)])
    else:
        ws.append_row(row_from_header(header, rec))

def export_clients_dataframe() -> pd.DataFrame:
    """Return dataframe with client export columns for registry creation."""
    df = _get_clients_df()
    if df.empty:
        return df
    cols = ["username","full_name","phone","city","address","postcode","created_at"]
    # Ensure columns exist
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]

# ========== Phone -> orders via clients & participants ==========
def get_orders_by_phone(phone: str) -> List[Dict[str, Any]]:
    digits = _digits_only(phone)
    if not digits:
        return []
    df = _get_clients_df()
    if df.empty:
        return []

    # match equals or endswith to allow short queries
    matches = df[(df["phone_digits"] == digits) | (df["phone_digits"].str.endswith(digits))]
    if matches.empty:
        return []
    usernames = set(matches["username"].astype(str).map(_normalize_username).tolist())

    # participants with any of these usernames
    parts = _ensure_ws("participants").get_all_records()
    order_ids = []
    seen = set()
    for r in parts:
        if _normalize_username(r.get("username")) in usernames:
            oid = str(r.get("order_id") or "").strip()
            if oid and oid not in seen:
                order_ids.append(oid); seen.add(oid)

    if not order_ids:
        return []
    index = {o.get("order_id"): o for o in list_orders()}
    return [index[oid] for oid in order_ids if oid in index]
