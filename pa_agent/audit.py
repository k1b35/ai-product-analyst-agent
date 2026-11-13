"""Журнал действий агента (JSONL). Пишется для каждого вызова инструмента."""
from __future__ import annotations

import functools
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable

from . import ROOT

AUDIT_PATH = ROOT / "audit.jsonl"


def _short(v: Any, n: int = 500) -> Any:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def log(tool: str, tier: str, params: dict, status: str, **extra) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": tool, "tier": tier, "status": status,
        "params": {k: _short(v) for k, v in params.items()},
        **{k: _short(v) for k, v in extra.items()},
    }
    path = Path(os.getenv("PA_AUDIT") or AUDIT_PATH)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def audited(tool: str, tier: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                out = fn(*args, **kwargs)
                log(tool, tier, kwargs, "ok", ms=int((time.time() - t0) * 1000))
                return out
            except Exception as e:  # ошибку тоже пишем, затем пробрасываем
                log(tool, tier, kwargs, "error", error=f"{type(e).__name__}: {e}")
                raise
        return wrapper
    return deco
