"""Статистика A/B: SRM, мощность, CUPED, эффект, поправки на множественность."""
from __future__ import annotations

import numpy as np
from scipy import stats
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize


def srm_test(counts: dict[str, int], expected_split: dict[str, float]) -> tuple[float, float]:
    """Хи-квадрат фактического распределения по группам против планового."""
    variants = list(expected_split)
    obs = np.array([counts.get(v, 0) for v in variants], dtype=float)
    exp = np.array([expected_split[v] for v in variants], dtype=float)
    exp = exp / exp.sum() * obs.sum()
    chi2, p = stats.chisquare(obs, exp)
    return float(chi2), float(p)


def required_n_per_group(baseline: float, mde_abs: float, metric_type: str,
                         sd: float | None = None, alpha: float = 0.05, power: float = 0.8) -> int:
    if mde_abs <= 0:
        raise ValueError("MDE должен быть > 0")
    if metric_type == "binary":
        es = proportion_effectsize(min(baseline + mde_abs, 0.999), baseline)
        n = NormalIndPower().solve_power(abs(es), alpha=alpha, power=power, ratio=1.0)
    else:
        if not sd:
            raise ValueError("Для continuous-метрики нужен sd")
        z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
        n = 2 * (z * sd / mde_abs) ** 2
    return int(np.ceil(n))


def achieved_mde(n_per_group: int, metric_type: str, baseline: float | None = None,
                 sd: float | None = None, alpha: float = 0.05, power: float = 0.8) -> float:
    """Минимальный абсолютный эффект, который тест с таким n обнаруживает с заданной мощностью."""
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    if metric_type == "binary":
        sd = float(np.sqrt(baseline * (1 - baseline)))
    return float(z * sd * np.sqrt(2.0 / n_per_group))


def cuped_adjust(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, float, float]:
    """CUPED: y_adj = y - theta * (x - mean(x)). Возвращает (y_adj, theta, доля снятой дисперсии)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    var_x = np.var(x, ddof=1)
    if var_x == 0:
        return y, 0.0, 0.0
    theta = np.cov(x, y, ddof=1)[0, 1] / var_x
    y_adj = y - theta * (x - x.mean())
    reduction = 1 - np.var(y_adj, ddof=1) / np.var(y, ddof=1)
    return y_adj, float(theta), float(reduction)


def winsorize(a: np.ndarray, hi_q: float = 0.995) -> np.ndarray:
    cap = np.quantile(a, hi_q)
    return np.minimum(a, cap)


def effect_binary(yc: np.ndarray, yt: np.ndarray, alpha: float = 0.05) -> dict:
    nc, nt = len(yc), len(yt)
    pc, pt = yc.mean(), yt.mean()
    diff = pt - pc
    pool = (yc.sum() + yt.sum()) / (nc + nt)
    se_pool = np.sqrt(pool * (1 - pool) * (1 / nc + 1 / nt))
    z = diff / se_pool if se_pool > 0 else 0.0
    p = float(2 * (1 - stats.norm.cdf(abs(z))))
    se = np.sqrt(pc * (1 - pc) / nc + pt * (1 - pt) / nt)
    q = stats.norm.ppf(1 - alpha / 2)
    return _pack(pc, pt, diff, diff - q * se, diff + q * se, p, nc, nt)


def effect_continuous(yc: np.ndarray, yt: np.ndarray, alpha: float = 0.05) -> dict:
    nc, nt = len(yc), len(yt)
    mc, mt = yc.mean(), yt.mean()
    diff = mt - mc
    se = np.sqrt(yc.var(ddof=1) / nc + yt.var(ddof=1) / nt)
    t_stat, p = stats.ttest_ind(yt, yc, equal_var=False)
    df = nc + nt - 2
    q = stats.t.ppf(1 - alpha / 2, df)
    return _pack(mc, mt, diff, diff - q * se, diff + q * se, float(p), nc, nt)


def _pack(mc, mt, diff, lo, hi, p, nc, nt) -> dict:
    return {
        "control_mean": float(mc), "treatment_mean": float(mt),
        "abs_diff": float(diff), "rel_diff": float(diff / mc) if mc else None,
        "ci_low": float(lo), "ci_high": float(hi), "p_value": float(p),
        "n_control": int(nc), "n_treatment": int(nt),
    }


def holm(pvals: list[float]) -> list[float]:
    """Поправка Холма (контроль FWER). Возвращает скорректированные p в исходном порядке."""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adj[idx] = min(1.0, running)
    return adj.tolist()


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    """Поправка Бенджамини-Хохберга (контроль FDR)."""
    m = len(pvals)
    order = np.argsort(pvals)[::-1]
    adj = np.empty(m)
    prev = 1.0
    for rank, idx in enumerate(order):
        i = m - rank
        prev = min(prev, pvals[idx] * m / i)
        adj[idx] = prev
    return adj.tolist()
