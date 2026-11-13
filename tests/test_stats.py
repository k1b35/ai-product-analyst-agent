import numpy as np
from pa_agent import stats_ab as S


def test_srm_detects_and_passes():
    _, p_bad = S.srm_test({"control": 10000, "treatment": 8800}, {"control": .5, "treatment": .5})
    _, p_ok = S.srm_test({"control": 10010, "treatment": 9990}, {"control": .5, "treatment": .5})
    assert p_bad < 1e-6 and p_ok > 0.5


def test_power_roundtrip():
    n = S.required_n_per_group(0.10, 0.015, "binary")
    assert 6000 < n < 9000
    mde = S.achieved_mde(n, "binary", baseline=0.10)
    assert abs(mde - 0.015) < 0.002


def test_cuped_reduces_variance_and_keeps_mean_effect():
    rng = np.random.default_rng(1)
    x = rng.normal(10, 3, 20000)
    y = 2 * x + rng.normal(0, 1, 20000)
    y_adj, theta, red = S.cuped_adjust(y, x)
    assert abs(theta - 2) < 0.05 and red > 0.9
    assert abs(y_adj.mean() - y.mean()) < 1e-9


def test_aa_false_positive_rate_near_alpha():
    rng = np.random.default_rng(2)
    fp = 0
    N = 400
    for _ in range(N):
        a, b = rng.binomial(1, .1, 3000), rng.binomial(1, .1, 3000)
        fp += S.effect_binary(a, b)["p_value"] < 0.05
    assert 0.02 < fp / N < 0.09


def test_holm_and_bh():
    p = [0.01, 0.04, 0.03]
    h, b = S.holm(p), S.benjamini_hochberg(p)
    assert all(hi >= pi for hi, pi in zip(h, p))
    assert all(bi <= hi + 1e-12 for bi, hi in zip(b, h))
    assert max(h) <= 1
