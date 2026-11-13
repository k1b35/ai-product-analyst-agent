"""Политика доступа: уровни автономности, SQL-guard, песочница файлов."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from . import ROOT


class PolicyError(Exception):
    """Действие запрещено политикой."""


class ApprovalRequired(PolicyError):
    """Действие допустимо только после подтверждения человеком."""


@dataclass
class Policy:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Policy":
        p = Path(path or os.getenv("PA_POLICY") or ROOT / "policy.yaml")
        return cls(yaml.safe_load(p.read_text(encoding="utf-8")))

    def get(self, *keys: str, default=None):
        cur: Any = self.raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def workspace(self) -> Path:
        return (ROOT / self.get("workspace", default="workspace")).resolve()

    def mode(self, tier: str) -> str:
        return self.get("autonomy", tier, default="deny")

    def require(self, tier: str, approved: bool = False) -> None:
        """Проверка уровня. approved=True означает, что человек уже подтвердил действие."""
        mode = self.mode(tier)
        if mode == "auto" or (mode == "approval" and approved):
            return
        if mode == "approval":
            raise ApprovalRequired(f"Действие уровня {tier} требует подтверждения человеком.")
        raise PolicyError(f"Действие уровня {tier} запрещено политикой.")


# ----------------------------------------------------------------------------
# SQL guard
# ----------------------------------------------------------------------------
_FORBIDDEN_NODES = tuple(
    getattr(exp, n) for n in (
        "Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Merge",
        "TruncateTable", "Copy", "Set", "Use", "Pragma", "Into", "Grant", "Revoke",
        "Transaction", "Commit", "Rollback", "Attach", "Detach", "LoadData",
    ) if hasattr(exp, n)
)
_FORBIDDEN_FUNCS = {
    "load_extension", "pg_read_file", "pg_read_binary_file", "lo_import", "lo_export",
    "dblink", "pg_sleep", "sleep", "xp_cmdshell", "readfile", "writefile", "edit",
    "pg_ls_dir", "copy",
}


def validate_select(sql: str, dialect: str | None = None) -> str:
    """Пропускает ровно один SELECT/WITH-запрос; иначе PolicyError."""
    if not sql or not sql.strip():
        raise PolicyError("Пустой SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:  # нераспознанный синтаксис = отказ
        raise PolicyError(f"SQL не разобран: {e}") from e
    if len(statements) != 1:
        raise PolicyError("Разрешён ровно один SQL-запрос.")
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union, exp.Subquery)):
        raise PolicyError("Разрешены только SELECT/WITH-запросы.")
    if stmt.find(*_FORBIDDEN_NODES):
        raise PolicyError("В запросе есть запрещённая операция.")
    for fn in stmt.find_all(exp.Func):
        name = (fn.name or fn.sql_name() or "").lower()
        if name in _FORBIDDEN_FUNCS:
            raise PolicyError(f"Запрещённая функция: {name}")
    return sql.strip().rstrip(";")


# ----------------------------------------------------------------------------
# Песочница файлов
# ----------------------------------------------------------------------------
def resolve_in_workspace(policy: Policy, rel_path: str) -> Path:
    if not rel_path or "\x00" in rel_path:
        raise PolicyError("Некорректный путь.")
    base = policy.workspace
    base.mkdir(parents=True, exist_ok=True)
    target = (base / rel_path).resolve()
    if target != base and base not in target.parents:
        raise PolicyError("Путь выходит за пределы рабочей папки.")
    exts = policy.get("allowed_extensions", default=[])
    if exts and target.suffix.lower() not in exts:
        raise PolicyError(f"Расширение {target.suffix or '(нет)'} не разрешено.")
    return target
