"""Проверка корректности A/B-теста и анализ эффекта с вердиктом."""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy.engine import Engine

from . import stats_ab as S
from .db import fetch_df

SRM_P_THRESHOLD = 0.001
PRE_BALANCE_P = 0.01
HEAVY_TAIL_KURTOSIS = 10.0


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}  # status: pass | warn | fail


def load_experiment(engine: Engine, experiment_id: str) -> tuple[dict, pd.DataFrame]:
    exp = fetch_df(engine, "SELECT * FROM experiments WHERE experiment_id = :id", {"id": experiment_id})
    if exp.empty:
        raise ValueError(f"Эксперимент {experiment_id} не найден")
    meta = exp.iloc[0].to_dict()
    meta["planned_split"] = json.loads(meta["planned_split"]) if meta.get("planned_split") else None
    data = fetch_df(engine, """
        SELECT a.user_id, a.variant, o.value, o.pre_value, o.guardrail
        FROM ab_assignments a
        LEFT JOIN ab_outcomes o ON o.experiment_id = a.experiment_id AND o.user_id = a.user_id
        WHERE a.experiment_id = :id""", {"id": experiment_id})
    return meta, data


def check_ab_test(engine: Engine, experiment_id: str, control: str = "control",
                  treatment: str = "treatment") -> dict[str, Any]:
    meta, df = load_experiment(engine, experiment_id)
    checks: list[dict] = []
    mtype = meta["metric_type"]
    alpha = float(meta.get("alpha") or 0.05)

    # 1. Дизайн зафиксирован заранее
    missing = [k for k in ("mde_abs", "planned_n_per_group", "planned_split", "alpha")
               if not meta.get(k)]
    checks.append(_check("design_preregistered", "warn" if missing else "pass",
                         f"Не заполнено: {', '.join(missing)}" if missing else "MDE, n, split, alpha заданы до старта"))

    # 2. SRM
    counts = df.groupby("variant").size().to_dict()
    srm_p = None
    if meta["planned_split"]:
        chi2, srm_p = S.srm_test(counts, meta["planned_split"])
        bad = srm_p < SRM_P_THRESHOLD
        checks.append(_check("srm", "fail" if bad else "pass",
                             f"Группы {counts}, план {meta['planned_split']}, chi2={chi2:.1f}, p={srm_p:.2g}"))

    yc_all = df[df.variant == control]
    yt_all = df[df.variant == treatment]
    n_c, n_t = len(yc_all), len(yt_all)

    # 3. Покрытие исходов
    missing_out = df["value"].isna().mean()
    checks.append(_check("outcome_coverage", "warn" if missing_out > 0.2 else "pass",
                         f"Доля пользователей без исхода: {missing_out:.1%} (считаются как 0 для binary/revenue)"))
    df["value"] = df["value"].fillna(0.0)
    if "pre_value" in df:
        df["pre_value"] = df["pre_value"].fillna(0.0)
    yc = df.loc[df.variant == control, "value"].to_numpy(float)
    yt = df.loc[df.variant == treatment, "value"].to_numpy(float)

    # 4. Баланс по pre-period
    if df["pre_value"].notna().any() and df["pre_value"].std() > 0:
        pc = df.loc[df.variant == control, "pre_value"].to_numpy(float)
        pt = df.loc[df.variant == treatment, "pre_value"].to_numpy(float)
        p_bal = stats.ttest_ind(pt, pc, equal_var=False).pvalue
        checks.append(_check("pre_period_balance", "warn" if p_bal < PRE_BALANCE_P else "pass",
                             f"Разница pre-метрики между группами: p={p_bal:.3f}"))

    # 5. Длительность: целые недели
    try:
        days = (pd.to_datetime(meta["end_date"]) - pd.to_datetime(meta["start_date"])).days + 1
        checks.append(_check("full_weeks", "pass" if days % 7 == 0 else "warn",
                             f"Длительность {days} дн." + ("" if days % 7 == 0 else " (не кратна 7: риск недельной сезонности)")))
    except Exception:
        pass

    # 6. Размер выборки и мощность
    planned = int(meta.get("planned_n_per_group") or 0)
    n_min = min(n_c, n_t)
    status = meta.get("status", "finished")
    if planned:
        reached = n_min >= planned * 0.95
        checks.append(_check("sample_size", "pass" if reached else "fail",
                             f"Минимальная группа {n_min}, план {planned} на группу"))
    base = float(np.mean(yc)) if len(yc) else 0.0
    sd = float(np.std(yc, ddof=1)) if len(yc) > 1 else None
    if n_min > 1:
        amde = S.achieved_mde(n_min, mtype, baseline=base, sd=sd, alpha=alpha)
        checks.append(_check("achieved_mde", "pass", f"При таком n тест надёжно видит эффект ≥ {amde:.4g} (абсолютный)"))

    # 7. Тяжёлые хвосты → винзоризация
    treat_note = None
    if mtype == "continuous" and len(df) > 100:
        kurt = float(stats.kurtosis(df["value"]))
        if kurt > HEAVY_TAIL_KURTOSIS:
            cap = np.quantile(np.r_[yc, yt], 0.995)  # общий порог для обеих групп (иначе смещение)
            yc, yt = np.minimum(yc, cap), np.minimum(yt, cap)
            treat_note = f"Тяжёлые хвосты (эксцесс {kurt:.0f}): метрика винзоризована на 99.5-м перцентиле"
            checks.append(_check("heavy_tails", "warn", treat_note))

    # Эффект
    effect_raw = (S.effect_binary if mtype == "binary" else S.effect_continuous)(yc, yt, alpha)
    effect = dict(effect_raw)
    # CUPED по pre_value (только continuous и binary с pre)
    cuped = None
    if df["pre_value"].std() > 0 and mtype == "continuous":
        y_all = np.r_[yc, yt]
        x_all = np.r_[df.loc[df.variant == control, "pre_value"].to_numpy(float),
                      df.loc[df.variant == treatment, "pre_value"].to_numpy(float)]
        y_adj, theta, red = S.cuped_adjust(y_all, x_all)
        if red > 0.05:
            e2 = S.effect_continuous(y_adj[:len(yc)], y_adj[len(yc):], alpha)
            cuped = {"theta": theta, "variance_reduction": red, **e2}
            effect = e2
            effect["cuped_applied"] = True
            checks.append(_check("cuped", "pass", f"CUPED снижает дисперсию на {red:.0%}"))

    # Guardrail + поправка Холма по двум метрикам
    guard = None
    if df["guardrail"].notna().any():
        g = df.copy()
        g["guardrail"] = g["guardrail"].fillna(0.0)
        gc = g.loc[g.variant == control, "guardrail"].to_numpy(float)
        gt = g.loc[g.variant == treatment, "guardrail"].to_numpy(float)
        guard = S.effect_continuous(gc, gt, alpha)
        adj = S.holm([effect["p_value"], guard["p_value"]])
        effect["p_value_holm"], guard["p_value_holm"] = adj
        checks.append(_check("multiple_metrics", "pass", "Primary и guardrail скорректированы по Холму"))
    else:
        effect["p_value_holm"] = effect["p_value"]

    sig = effect["p_value_holm"] < alpha
    guard_bad = bool(guard and guard["p_value_holm"] < alpha and guard["abs_diff"] < 0)

    # Вердикт
    failed = {c["name"] for c in checks if c["status"] == "fail"}
    if "srm" in failed:
        verdict = "invalid"
        reason = "Нарушено планируемое распределение по группам (SRM): эффект не интерпретируется."
    elif status == "running" and planned and n_min < planned:
        verdict = "in_progress"
        reason = "Тест не набрал плановый размер выборки: решение по ранним данным (peeking) недопустимо."
    elif "sample_size" in failed:
        verdict = "underpowered"
        reason = ("Выборка меньше плановой. " + ("Эффект значим, но оценка при малой выборке завышается (winner's curse): "
                  "перепроверить на плановом n." if sig else "Эффекта не видно, но «нет эффекта» утверждать нельзя."))
    elif sig and effect["abs_diff"] > 0 and guard_bad:
        verdict = "guardrail_violation"
        reason = "Primary улучшилась, но guardrail значимо ухудшился."
    elif sig and effect["abs_diff"] > 0:
        verdict = "valid_win"
        reason = "Значимый положительный эффект при пройденных проверках."
    elif sig:
        verdict = "valid_loss"
        reason = "Значимый отрицательный эффект."
    else:
        verdict = "valid_no_effect"
        reason = "Значимого эффекта нет; тест достаточно мощный для эффекта не меньше MDE."

    return {
        "experiment_id": experiment_id, "name": meta.get("name"), "primary_metric": meta.get("primary_metric"),
        "metric_type": mtype, "status": status, "verdict": verdict, "reason": reason,
        "checks": checks, "effect": effect, "guardrail": guard, "cuped": cuped,
        "groups": {"control": n_c, "treatment": n_t}, "srm_p": srm_p, "alpha": alpha,
        "planned_n_per_group": planned or None,
    }
