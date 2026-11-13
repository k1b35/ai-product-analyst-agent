"""Реестр гипотез. Гарантии зашиты в код, а не в промпт:
- 'causal' допустим только со ссылкой на A/B с валидным вердиктом;
- confirmed требует causal или явного 'observed/correlational' → максимум 'supported' не выдаём;
- множественные проверки одной гипотезы корректируются по BH."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .policy import PolicyError
from .state import conn
from .stats_ab import benjamini_hochberg

STATUSES = {"proposed", "testing", "confirmed", "rejected", "inconclusive"}
CONFIDENCE = {"observed", "correlational", "causal"}
VALID_VERDICTS = {"valid_win", "valid_loss", "valid_no_effect", "guardrail_violation"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(title: str, statement: str, metric: str) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO hypotheses(ts,title,statement,metric,evidence,updated_ts) VALUES(?,?,?,?,?,?)",
                        (_now(), title, statement, metric, "[]", _now()))
        return cur.lastrowid


def update(hid: int, status: str, confidence: str, evidence: dict, experiment_id: str | None = None,
           ab_verdict: str | None = None) -> dict:
    if status not in STATUSES or confidence not in CONFIDENCE:
        raise PolicyError("Недопустимый статус или уровень уверенности.")
    if confidence == "causal" and ab_verdict not in VALID_VERDICTS:
        raise PolicyError("Причинная уверенность допустима только при валидном A/B-вердикте.")
    if status in {"confirmed", "rejected"} and confidence != "causal":
        raise PolicyError("confirmed/rejected допустимы только с causal-доказательством (валидный A/B). "
                          "Для наблюдательных данных используйте 'inconclusive' или оставьте 'testing'.")
    with conn() as c:
        row = c.execute("SELECT evidence FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if not row:
            raise PolicyError("Гипотеза не найдена.")
        ev = json.loads(row["evidence"])
        ev.append({"ts": _now(), **evidence})
        c.execute("UPDATE hypotheses SET status=?, confidence=?, evidence=?, experiment_id=?, updated_ts=? WHERE id=?",
                  (status, confidence, json.dumps(ev, ensure_ascii=False), experiment_id, _now(), hid))
    return {"id": hid, "status": status, "confidence": confidence}


def list_all(status: str | None = None) -> list[dict]:
    with conn() as c:
        q = "SELECT * FROM hypotheses" + (" WHERE status=?" if status else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q, (status,) if status else ())]


def adjust_pvalues(pvals: dict[str, float]) -> dict[str, float]:
    keys = list(pvals)
    adj = benjamini_hochberg([pvals[k] for k in keys])
    return dict(zip(keys, adj))
