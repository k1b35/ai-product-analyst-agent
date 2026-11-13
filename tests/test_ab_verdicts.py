import pytest

from pa_agent import reports
from pa_agent.ab import check_ab_test
from pa_agent.db import run_select


def test_all_verdicts_match_ground_truth(engine, truth):
    got = {e: check_ab_test(engine, e)["verdict"] for e in truth["experiments"]}
    assert got == truth["experiments"]


def test_srm_experiment_is_invalid_and_pm_is_warned(engine):
    r = check_ab_test(engine, "exp_feed_redesign")
    assert r["verdict"] == "invalid" and r["srm_p"] < 0.001
    assert "Результат использовать нельзя" in reports.ab_report(r, "pm")


def test_srm_root_cause_is_findable_with_sql(engine, policy):
    """Путь, которым должен пройти агент: разрез сплита по платформам показывает потерю android в treatment."""
    r = run_select(engine, policy, """
        SELECT u.platform, a.variant, COUNT(*) AS n FROM ab_assignments a JOIN users u USING(user_id)
        WHERE a.experiment_id = 'exp_feed_redesign' GROUP BY 1, 2""")
    n = {(x["platform"], x["variant"]): x["n"] for x in r["rows"]}
    ratio = {p: n[(p, "treatment")] / n[(p, "control")] for p in ("ios", "android", "web")}
    assert ratio["android"] < 0.75 and abs(ratio["ios"] - 1) < 0.1


def test_underpowered_is_not_no_effect(engine):
    r = check_ab_test(engine, "exp_portfolio_tips")
    assert r["verdict"] == "underpowered"
    assert "Решения нет" in reports.ab_report(r, "pm")


def test_guardrail_blocks_rollout(engine):
    r = check_ab_test(engine, "exp_copytrading_cta")
    assert r["verdict"] == "guardrail_violation" and r["effect"]["abs_diff"] > 0
    assert "Не раскатывать" in reports.ab_report(r, "pm")


def test_heavy_tail_gets_winsorized_and_cuped(engine):
    names = {c["name"] for c in check_ab_test(engine, "exp_tariff_showcase")["checks"]}
    assert {"heavy_tails", "cuped"} <= names


def test_report_audiences_differ(engine):
    r = check_ab_test(engine, "exp_onboarding_v2")
    pm, an = reports.ab_report(r, "pm"), reports.ab_report(r, "analyst")
    assert "Раскатывать" in pm and "p(Holm)" in an and "p(Holm)" not in pm


def test_unknown_experiment(engine):
    with pytest.raises(ValueError):
        check_ab_test(engine, "nope")
