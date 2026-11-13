"""Мониторинг метрик и качества данных: сезонный robust z-score, CUSUM, drill-down по сегментам."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from .db import fetch_df
from .policy import Policy

Z_MIN = 3.0
MIN_HISTORY_WEEKS = 3


def expected_value(s: pd.Series, weeks: int = 8) -> tuple[float, float]:
    """Ожидание для последней точки с учётом тренда и недельной сезонности.

    expected = средний уровень 7 предыдущих дней x коэффициент дня недели,
    где коэффициент = медиана отношения «значение / среднее 7 предыдущих дней» по тем же дням недели.
    Разброс — робастный (MAD) по остаткам за последние недели. Последняя точка в расчёт не входит."""
    hist = s.iloc[:-1].astype(float)
    if len(hist) < 14:
        med = float(np.median(hist))
        return med, max(float(np.median(np.abs(hist - med))) * 1.4826, 0.01 * abs(med), 1e-9)
    m7 = hist.rolling(7).mean().shift(1)
    ratio = (hist / m7).dropna().iloc[-weeks * 7:]
    wd = np.array([d.weekday() for d in ratio.index])
    target_wd = s.index[-1].weekday()
    factors = {w: float(np.median(ratio[wd == w])) if (wd == w).sum() >= 2 else 1.0 for w in range(7)}
    level = float(hist.iloc[-7:].mean())
    exp_v = level * factors[target_wd]
    resid = ratio.values / np.array([factors[w] for w in wd]) - 1
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    sigma = max(mad * abs(exp_v), 0.01 * abs(exp_v), 1e-9)
    return exp_v, sigma


expected_same_weekday = expected_value  # обратная совместимость


def cusum(resid: np.ndarray, k: float = 0.5, h: float = 5.0) -> dict:
    """Двусторонний CUSUM по стандартизованным остаткам: ловит медленный дрейф."""
    pos = neg = 0.0
    alarm = None
    for i, r in enumerate(resid):
        pos = max(0.0, pos + r - k)
        neg = max(0.0, neg - r - k)
        if alarm is None and (pos > h or neg > h):
            alarm = ("up" if pos > h else "down", i)
    return {"alarm": alarm, "pos": pos, "neg": neg}


def _series(df: pd.DataFrame) -> pd.Series:
    s = df.assign(date=pd.to_datetime(df["date"])).set_index("date")["value"].astype(float)
    return s.sort_index()


def check_metric(engine: Engine, m: dict) -> dict[str, Any]:
    df = fetch_df(engine, m["sql"])
    if len(df) < 14:
        return {"metric": m["name"], "status": "insufficient_data", "severity": "info"}
    s = _series(df)
    exp_v, sigma = expected_value(s)
    val = float(s.iloc[-1])
    pct = (val - exp_v) / exp_v * 100 if exp_v else 0.0
    z = (val - exp_v) / sigma
    bad_pct = -pct if m.get("bad_direction", "down") == "down" else pct
    sev = "ok"
    if bad_pct >= m["critical_pct"] and abs(z) >= Z_MIN:
        sev = "critical"
    elif bad_pct >= m["warn_pct"] and abs(z) >= Z_MIN:
        sev = "warning"
    # CUSUM по последним 28 дням (остатки относительно сезонной медианы по дню недели)
    resid = []
    for i in range(max(14, len(s) - 28), len(s)):
        sub = s.iloc[: i + 1]
        e, sg = expected_value(sub)
        resid.append((sub.iloc[-1] - e) / sg)
    cs = cusum(np.array(resid[:-1]))
    # Тренд: средний уровень прошлой недели (без сегодняшнего дня) против той же недели 4 недели назад
    trend_pct = None
    kind = "spike" if sev != "ok" else None
    if len(s) >= 36:
        recent, prev = float(s.iloc[-8:-1].mean()), float(s.iloc[-36:-29].mean())
        trend_pct = (recent / prev - 1) * 100 if prev else 0.0
        bad_trend = -trend_pct if m.get("bad_direction", "down") == "down" else trend_pct
        if sev == "ok" and bad_trend >= m["warn_pct"]:
            sev, kind = "warning", "trend"
    out = {
        "metric": m["name"], "date": s.index[-1].date().isoformat(), "value": val, "kind": kind,
        "trend_4w_pct": trend_pct,
        "expected": exp_v, "pct_vs_expected": pct, "robust_z": z, "severity": sev,
        "owner": m.get("owner"), "drift": cs["alarm"] is not None,
        "drift_direction": cs["alarm"][0] if cs["alarm"] else None,
        "status": "anomaly" if sev != "ok" else "ok",
    }
    if sev != "ok" and m.get("segment_sql"):
        out["drill_down"] = drill_down(engine, m, s.index[-1])
    return out


def drill_down(engine: Engine, m: dict, day: pd.Timestamp) -> list[dict]:
    """Вклад сегментов в отклонение: кто упал сильнее всего относительно своего ожидания."""
    df = fetch_df(engine, m["segment_sql"])
    df["date"] = pd.to_datetime(df["date"])
    rows = []
    for seg, g in df.groupby("segment"):
        s = g.set_index("date")["value"].astype(float).sort_index()
        if len(s) < 14 or s.index[-1] != day:
            continue
        e, sg = expected_value(s)
        v = float(s.iloc[-1])
        rows.append({"segment": seg, "value": v, "expected": e, "delta": v - e,
                     "pct": (v - e) / e * 100 if e else 0.0, "z": (v - e) / sg})
    total = sum(r["delta"] for r in rows) or 1e-9
    for r in rows:
        r["share_of_change"] = r["delta"] / total
    return sorted(rows, key=lambda r: r["delta"])


def check_data_quality(engine: Engine, policy: Policy, today: date | None = None) -> list[dict]:
    today = today or date.today()
    res = []
    for c in policy.get("data_checks", default=[]) or []:
        df = fetch_df(engine, c["sql"])
        last = df.iloc[0, 0]
        if last is None or pd.isna(last):
            res.append({"check": c["name"], "status": "fail", "detail": "таблица пуста"})
            continue
        last_d = pd.to_datetime(last).date()
        lag = (today - last_d).days
        ok = lag <= c["max_lag_days"]
        res.append({"check": c["name"], "status": "ok" if ok else "fail",
                    "last_date": last_d.isoformat(), "lag_days": lag,
                    "detail": f"последняя дата {last_d}, отставание {lag} дн. (допустимо {c['max_lag_days']})"})
    return res


def run_monitor(engine: Engine, policy: Policy, today: date | None = None) -> dict:
    metrics = [check_metric(engine, m) for m in policy.get("metrics", default=[])]
    dq = check_data_quality(engine, policy, today)
    return {"metrics": metrics, "data_quality": dq,
            "has_issues": any(m["severity"] in ("warning", "critical") for m in metrics)
                          or any(d["status"] == "fail" for d in dq)}


def format_alert(m: dict, dq_failed: list[dict]) -> str:
    sev = {"critical": "🔴 CRITICAL", "warning": "🟠 WARNING"}.get(m["severity"], "ℹ️")
    lines = [f"{sev}: {m['metric']} {m['date']}"]
    if m.get("kind") == "trend":
        lines.append(f"Плавное ухудшение: средний уровень за неделю {m['trend_4w_pct']:+.1f}% к уровню 4 недели назад")
    else:
        lines.append(f"Факт {m['value']:.4g} vs ожидание {m['expected']:.4g} ({m['pct_vs_expected']:+.1f}%, z={m['robust_z']:.1f})")
    dd = m.get("drill_down") or []
    if dd:
        w = dd[0]
        lines.append(f"Главный вклад: {w['segment']} ({w['pct']:+.0f}%, {w['share_of_change']:.0%} падения)")
    if dq_failed:
        lines.append("⚠️ Данные могут быть неполными: " + "; ".join(d["check"] for d in dq_failed))
    lines.append(f"Владелец: {m.get('owner')}")
    return "\n".join(lines)
