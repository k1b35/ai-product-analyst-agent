"""Telegram: алерты с дедупликацией/cooldown/rate-limit и подтверждение T3-действий.
Без токена работает в dry-run: сообщения пишутся в таблицу outbox."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

from .policy import Policy
from .state import conn

SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deliver(policy: Policy, text: str, reply_markup: dict | None = None) -> str:
    token = os.getenv(policy.get("telegram", "token_env", default="TELEGRAM_BOT_TOKEN"))
    chat = os.getenv(policy.get("telegram", "chat_id_env", default="TELEGRAM_CHAT_ID"))
    if not token or not chat:
        status = "dry_run"
    else:
        payload = {"chat_id": chat, "text": text}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=15)
        status = "sent" if r.ok else f"error_{r.status_code}"
    with conn() as c:
        c.execute("INSERT INTO outbox(ts,chat,text,status) VALUES(?,?,?,?)",
                  (_now().isoformat(), chat or "-", text, status))
    return status


def send_alert(policy: Policy, key: str, severity: str, text: str) -> dict:
    """Отправка с защитой от спама: cooldown по ключу (эскалация severity пробивает cooldown) и лимит в час."""
    cooldown = timedelta(hours=policy.get("telegram", "alert_cooldown_hours", default=12))
    limit = policy.get("telegram", "max_messages_per_hour", default=20)
    now = _now()
    with conn() as c:
        last = c.execute("SELECT ts, severity FROM alerts WHERE key=? AND status!='suppressed' ORDER BY ts DESC LIMIT 1",
                         (key,)).fetchone()
        if last and now - datetime.fromisoformat(last["ts"]) < cooldown \
                and SEV_RANK.get(severity, 0) <= SEV_RANK.get(last["severity"], 0):
            c.execute("INSERT INTO alerts VALUES(?,?,?,?,?)", (key, now.isoformat(), severity, "suppressed", text))
            return {"sent": False, "reason": "cooldown"}
        hour_ago = (now - timedelta(hours=1)).isoformat()
        n = c.execute("SELECT COUNT(*) FROM alerts WHERE ts>? AND status!='suppressed'", (hour_ago,)).fetchone()[0]
        if n >= limit:
            c.execute("INSERT INTO alerts VALUES(?,?,?,?,?)", (key, now.isoformat(), severity, "suppressed", text))
            return {"sent": False, "reason": "rate_limit"}
    status = _deliver(policy, text)
    with conn() as c:
        c.execute("INSERT INTO alerts VALUES(?,?,?,?,?)", (key, now.isoformat(), severity, status, text))
    return {"sent": True, "status": status}


# --- Подтверждение T3 ---------------------------------------------------------
def request_approval(policy: Policy, action: str, payload: dict) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO approvals(ts,action,payload) VALUES(?,?,?)",
                        (_now().isoformat(), action, json.dumps(payload, ensure_ascii=False)))
        aid = cur.lastrowid
    kb = {"inline_keyboard": [[{"text": "✅ Одобрить", "callback_data": f"ap:{aid}:yes"},
                                {"text": "❌ Отклонить", "callback_data": f"ap:{aid}:no"}]]}
    _deliver(policy, f"🔐 Запрос на действие #{aid}: {action}\n{json.dumps(payload, ensure_ascii=False)[:800]}", kb)
    return aid


def decide(policy: Policy, approval_id: int, user_id: int, approve: bool) -> dict:
    """Решение принимается только от пользователей из whitelist approvers."""
    if user_id not in (policy.get("telegram", "approvers", default=[]) or []):
        return {"ok": False, "reason": "user_not_in_approvers"}
    with conn() as c:
        row = c.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "not_found"}
        if row["status"] != "pending":
            return {"ok": False, "reason": f"already_{row['status']}"}
        c.execute("UPDATE approvals SET status=?, decided_by=?, decided_ts=? WHERE id=?",
                  ("approved" if approve else "rejected", user_id, _now().isoformat(), approval_id))
    return {"ok": True, "status": "approved" if approve else "rejected"}


def is_approved(approval_id: int) -> bool:
    with conn() as c:
        row = c.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()
    return bool(row and row["status"] == "approved")


def poll_updates(policy: Policy, offset: int | None = None) -> int | None:
    """Забирает нажатия кнопок из Telegram (getUpdates). В dry-run ничего не делает."""
    token = os.getenv(policy.get("telegram", "token_env", default="TELEGRAM_BOT_TOKEN"))
    if not token:
        return offset
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                     params={"offset": offset, "timeout": 0}, timeout=15).json()
    for u in r.get("result", []):
        offset = u["update_id"] + 1
        cq = u.get("callback_query")
        if cq and cq.get("data", "").startswith("ap:"):
            _, aid, ans = cq["data"].split(":")
            decide(policy, int(aid), cq["from"]["id"], ans == "yes")
    return offset
