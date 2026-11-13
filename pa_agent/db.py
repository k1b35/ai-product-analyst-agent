"""Подключение к БД. По умолчанию демо-SQLite в режиме read-only."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from . import ROOT
from .policy import Policy, validate_select

_DEADLINES: dict[int, dict] = {}


def get_engine(url: str | None = None, timeout_s: int = 30) -> Engine:
    if not (url or os.getenv("PA_DB_URL")):
        from synth import default_db_path
        path = default_db_path()
        if not path.exists():
            raise FileNotFoundError(f"Нет БД {path}. Сгенерируйте синтетический мир: python -m synth.generate")
        url = f"sqlite:///file:{path}?mode=ro&uri=true"
    url = url or os.getenv("PA_DB_URL")
    eng = create_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _guard(dbapi_conn, _rec):  # read-only + таймаут запроса
            dbapi_conn.execute("PRAGMA query_only = ON")
            deadline = {"t": float("inf")}
            dbapi_conn.set_progress_handler(lambda: 1 if time.time() > deadline["t"] else 0, 10000)
            _DEADLINES[id(dbapi_conn)] = deadline
    return eng


def fetch_df(engine: Engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Внутренние (доверенные) запросы кода агента. Не отдавать агенту напрямую."""
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})


def run_select(engine: Engine, policy: Policy, sql: str) -> dict:
    """Запрос агента: guard, лимит строк, таймаут. Возвращает таблицу и признак усечения."""
    clean = validate_select(sql, dialect=engine.dialect.name if engine.dialect.name != "sqlite" else "sqlite")
    max_rows = int(policy.get("sql", "max_rows", default=10000))
    timeout = float(policy.get("sql", "timeout_s", default=30))
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        dl = _DEADLINES.get(id(raw))
        if dl is not None:
            dl["t"] = time.time() + timeout
        try:
            res = conn.execute(text(clean))
            cols = list(res.keys())
            rows = res.fetchmany(max_rows + 1)
        finally:
            if dl is not None:
                dl["t"] = float("inf")
    truncated = len(rows) > max_rows
    df = pd.DataFrame(rows[:max_rows], columns=cols)
    return {"columns": cols, "rows": df.to_dict("records"), "n_rows": len(df), "truncated": truncated}
