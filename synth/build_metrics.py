"""Пайплайн агрегатов: сырые daily_activity -> daily_metrics, daily_metrics_by_platform.
Идемпотентен (полная пересборка). Это тот пайплайн, который агент запускает сам (T2), когда видит, что витрины устарели.

    python -m synth.build_metrics                # до последней даты сырых данных
    python -m synth.build_metrics --until 2026-11-09
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from . import default_db_path

AGG_SQL = """
DELETE FROM daily_metrics;
DELETE FROM daily_metrics_by_platform;
INSERT INTO daily_metrics(date, metric, value)
  SELECT date, 'dau', COUNT(*) FROM daily_activity WHERE date <= :until GROUP BY date;
INSERT INTO daily_metrics(date, metric, value)
  SELECT date, 'conversion_rate', AVG(CASE WHEN trades > 0 THEN 1.0 ELSE 0.0 END)
  FROM daily_activity WHERE date <= :until GROUP BY date;
INSERT INTO daily_metrics(date, metric, value)
  SELECT date, 'revenue', SUM(commission_rub) FROM daily_activity WHERE date <= :until GROUP BY date;
INSERT INTO daily_metrics_by_platform(date, platform, metric, value)
  SELECT date, platform, 'dau', COUNT(*) FROM daily_activity WHERE date <= :until GROUP BY date, platform;
INSERT INTO daily_metrics_by_platform(date, platform, metric, value)
  SELECT date, platform, 'conversion_rate', AVG(CASE WHEN trades > 0 THEN 1.0 ELSE 0.0 END)
  FROM daily_activity WHERE date <= :until GROUP BY date, platform;
INSERT INTO daily_metrics_by_platform(date, platform, metric, value)
  SELECT date, platform, 'revenue', SUM(commission_rub) FROM daily_activity WHERE date <= :until GROUP BY date, platform;
"""


def build(db: Path | str | None = None, until: str | None = None) -> dict:
    db = Path(db or default_db_path())
    con = sqlite3.connect(db)
    until = until or con.execute("SELECT MAX(date) FROM daily_activity").fetchone()[0]
    with con:
        for stmt in [s.strip() for s in AGG_SQL.split(";") if s.strip()]:
            con.execute(stmt, {"until": until} if ":until" in stmt else {})
        con.execute("INSERT INTO pipeline_log(ts, pipeline, until, status) VALUES(datetime('now'), 'build_metrics', ?, 'ok')",
                    (until,))
    n = con.execute("SELECT COUNT(DISTINCT date) FROM daily_metrics").fetchone()[0]
    con.close()
    return {"until": until, "days": n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--until")
    a = ap.parse_args()
    r = build(a.db, a.until)
    print(f"build_metrics: витрины пересобраны до {r['until']} ({r['days']} дн.)")
