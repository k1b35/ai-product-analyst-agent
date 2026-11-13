"""Запуск пайплайнов только из allowlist (policy.yaml): без shell, с таймаутом, dry-run и min-interval."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

from . import ROOT
from .policy import Policy, PolicyError
from .state import conn


def run_pipeline(policy: Policy, name: str, dry_run: bool = False, timeout_s: int = 300) -> dict:
    spec = (policy.get("pipelines", default={}) or {}).get(name)
    if not spec:
        raise PolicyError(f"Пайплайн '{name}' не в allowlist.")
    # неидемпотентные пайплайны считаются записью в прод
    policy.require("t2_pipelines" if spec.get("idempotent") else "t3_prod")
    cmd = [sys.executable if c == "python" else c for c in spec["cmd"]]
    if dry_run:
        return {"name": name, "dry_run": True, "cmd": cmd}
    min_int = timedelta(minutes=spec.get("min_interval_minutes", 0))
    now = datetime.now(timezone.utc)
    with conn() as c:
        last = c.execute("SELECT ts FROM pipeline_runs WHERE name=? AND status='ok' ORDER BY id DESC LIMIT 1",
                         (name,)).fetchone()
    if last and now - datetime.fromisoformat(last["ts"]) < min_int:
        return {"name": name, "skipped": True, "reason": "min_interval"}
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, shell=False)
        status, out = ("ok" if r.returncode == 0 else "failed"), (r.stdout + r.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        status, out = "timeout", ""
    with conn() as c:
        c.execute("INSERT INTO pipeline_runs(name,ts,status,output) VALUES(?,?,?,?)",
                  (name, now.isoformat(), status, out))
    return {"name": name, "status": status, "output": out}
