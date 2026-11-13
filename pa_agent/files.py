"""Файлы агента: только внутри workspace/, с allowlist расширений, бэкапом и diff."""
from __future__ import annotations

import difflib
import shutil
from datetime import datetime

from .policy import Policy, PolicyError, resolve_in_workspace


def read_file(policy: Policy, rel_path: str) -> str:
    p = resolve_in_workspace(policy, rel_path)
    if not p.exists():
        raise PolicyError(f"Файл не найден: {rel_path}")
    return p.read_text(encoding="utf-8")


def write_file(policy: Policy, rel_path: str, content: str) -> dict:
    policy.require("t1_sandbox")
    if len(content.encode("utf-8")) > policy.get("max_file_bytes", default=200000):
        raise PolicyError("Файл слишком большой.")
    p = resolve_in_workspace(policy, rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    backup = None
    if p.exists():
        backup = p.with_suffix(p.suffix + f".bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(p, backup)
    p.write_text(content, encoding="utf-8")
    diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True),
                                        f"a/{rel_path}", f"b/{rel_path}", n=2))
    return {"path": rel_path, "created": not old and backup is None,
            "backup": backup.name if backup else None, "diff": diff[:4000]}


def list_files(policy: Policy) -> list[str]:
    ws = policy.workspace
    ws.mkdir(parents=True, exist_ok=True)
    return sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file() and ".bak-" not in p.name)
