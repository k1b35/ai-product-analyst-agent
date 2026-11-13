from datetime import date

import numpy as np
import pandas as pd

from pa_agent import monitor as M


def test_stale_aggregates_are_flagged_before_pipeline(engine, policy):
    dq = {d["check"]: d for d in M.check_data_quality(engine, policy)}
    assert dq["aggregates_freshness"]["status"] == "fail" and dq["aggregates_freshness"]["lag_days"] == 4
    assert dq["raw_activity_freshness"]["status"] == "ok"


def test_fresh_after_pipeline(fresh_engine, policy):
    assert all(d["status"] == "ok" for d in M.check_data_quality(fresh_engine, policy))


def test_android_crash_detected_and_attributed(fresh_engine, policy, truth):
    res = {m["metric"]: m for m in M.run_monitor(fresh_engine, policy)["metrics"]}
    dau = res["dau"]
    assert dau["severity"] in ("warning", "critical") and dau["kind"] == "spike"
    assert dau["drill_down"][0]["segment"] == truth["incidents"]["android_crash"]["segment"]
    assert dau["drill_down"][0]["share_of_change"] > 0.8


def test_conversion_mix_shift_detected_as_trend(fresh_engine, policy):
    conv = {m["metric"]: m for m in M.run_monitor(fresh_engine, policy)["metrics"]}["conversion_rate"]
    assert conv["severity"] == "warning" and conv["kind"] == "trend" and conv["trend_4w_pct"] < -8


def test_stale_view_hides_the_crash(engine, policy):
    """Пока витрины старые, падения не видно: поэтому агент обязан сначала проверить свежесть данных."""
    dau = {m["metric"]: m for m in M.run_monitor(engine, policy)["metrics"]}["dau"]
    assert dau["kind"] != "spike"


def test_weekly_seasonality_and_growth_do_not_trigger():
    """Рост +40% за 10 недель и выходные -20%: базовая линия не смещена, ложных |z|>3 мало."""
    idx = pd.date_range(end=date.today(), periods=70)
    clean = pd.Series(np.linspace(100, 140, len(idx)) * np.array([0.8 if d.weekday() >= 5 else 1.0 for d in idx]), index=idx)
    zs, bias = [], []
    for seed in range(200):
        s = clean * np.random.default_rng(seed).normal(1, 0.01, len(clean))
        e, sg = M.expected_value(s)
        zs.append((s.iloc[-1] - e) / sg)
        bias.append(e / clean.iloc[-1] - 1)
    assert abs(np.mean(bias)) < 0.01
    assert np.mean(np.abs(zs) > 3) < 0.05


def test_cusum_catches_slow_drift():
    r = np.r_[np.zeros(20), np.full(15, 1.0)]
    assert M.cusum(r)["alarm"] is not None
    assert M.cusum(np.zeros(35))["alarm"] is None
