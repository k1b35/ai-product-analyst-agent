"""Локальное состояние агента (SQLite): алерты, outbox, одобрения, запуски пайплайнов, гипотезы."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  key TEXT, ts TEXT, severity TEXT, status TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chat TEXT, text TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT, payload TEXT,
  status TEXT DEFAULT 'pending', decided_by INTEGER, decided_ts TEXT);
CREATE TABLE IF NOT EXISTS pipeline_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts TEXT, status TEXT, output TEXT);
CREATE TABLE IF NOT EXISTS hypotheses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, statement TEXT, metric TEXT,
  status TEXT DEFAULT 'proposed', confidence TEXT DEFAULT 'observed', evidence TEXT, experiment_id TEXT,
  updated_ts TEXT);
"""


def state_path() -> Path:
    return Path(os.getenv("PA_STATE") or ROOT / "state.db")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(state_path())
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c
