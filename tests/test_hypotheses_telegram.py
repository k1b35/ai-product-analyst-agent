import pytest
from pa_agent import hypotheses as H, telegram as T
from pa_agent.policy import PolicyError, resolve_in_workspace
from pa_agent.state import conn
from pa_agent.toolbox import Toolbox


def test_confirmed_requires_causal(policy):
    hid = H.add("t", "s", "conversion")
    with pytest.raises(PolicyError):
        H.update(hid, "confirmed", "correlational", {"n": 1})
    with pytest.raises(PolicyError):
        H.update(hid, "testing", "causal", {"n": 1}, ab_verdict="invalid")
    H.update(hid, "testing", "correlational", {"n": 1})
    assert H.update(hid, "confirmed", "causal", {"n": 1}, "exp_onboarding_v2", "valid_win")["status"] == "confirmed"


def test_agent_cannot_confirm_via_invalid_experiment(engine, policy):
    box = Toolbox(policy, engine)
    hid = box.call("add_hypothesis", title="t", statement="s", metric="conversion")["id"]
    r = box.call("update_hypothesis", hypothesis_id=hid, status="confirmed", confidence="causal",
                 evidence="по SRM-тесту", experiment_id="exp_feed_redesign")
    assert "error" in r
    ok = box.call("update_hypothesis", hypothesis_id=hid, status="confirmed", confidence="causal",
                  evidence="онбординг", experiment_id="exp_onboarding_v2")
    assert ok.get("status") == "confirmed"


def test_alert_dedupe_cooldown_and_escalation(policy):
    assert T.send_alert(policy, "k", "warning", "a")["sent"]
    assert T.send_alert(policy, "k", "warning", "a")["reason"] == "cooldown"
    assert T.send_alert(policy, "k", "critical", "b")["sent"]          # эскалация пробивает cooldown
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
        assert c.execute("SELECT status FROM outbox LIMIT 1").fetchone()[0] == "dry_run"


def test_rate_limit(policy):
    policy.raw["telegram"]["max_messages_per_hour"] = 3
    sent = [T.send_alert(policy, f"k{i}", "warning", "x")["sent"] for i in range(6)]
    assert sent == [True, True, True, False, False, False]


def test_approval_whitelist(policy):
    aid = T.request_approval(policy, "stop_experiment", {"id": "exp_x"})
    assert not T.is_approved(aid)
    assert T.decide(policy, aid, 999, True)["reason"] == "user_not_in_approvers"
    assert T.decide(policy, aid, 111111111, True)["ok"]
    assert T.is_approved(aid)
    assert T.decide(policy, aid, 111111111, False)["reason"] == "already_approved"


def test_pipeline_allowlist_dry_run_and_real_run(policy, world, tmp_path, monkeypatch):
    import shutil
    from pa_agent.db import get_engine
    from pa_agent.monitor import check_data_quality
    copy = tmp_path / "w.db"
    shutil.copy(world, copy)
    monkeypatch.setenv("PA_SQLITE_PATH", str(copy))
    box = Toolbox(policy, get_engine(f"sqlite:///file:{copy}?mode=ro&uri=true"))
    assert "error" in box.call("run_pipeline", name="rm_rf")
    assert box.call("run_pipeline", name="build_metrics", dry_run=True)["dry_run"]
    r = box.call("run_pipeline", name="build_metrics")
    assert r["status"] == "ok", r
    assert all(d["status"] == "ok" for d in check_data_quality(box.engine, policy))
    assert box.call("run_pipeline", name="build_metrics")["reason"] == "min_interval"


def test_audit_written(policy, engine, tmp_path):
    Toolbox(policy, engine).call("run_sql", sql="DROP TABLE x")
    assert "denied" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_prompt_injection_in_data_is_returned_as_data(engine, policy, truth):
    """Пост с «инструкцией» для ИИ возвращается как данные и не даёт новых прав."""
    box = Toolbox(policy, engine)
    pid = truth["incidents"]["prompt_injection"]["post_id"]
    r = box.call("run_sql", sql=f"SELECT text FROM posts WHERE post_id = {pid}")
    assert "DROP TABLE users" in r["rows"][0]["text"]
    assert "error" in box.call("run_sql", sql="DROP TABLE users")


def test_ground_truth_is_not_readable_by_agent(policy):
    with pytest.raises(PolicyError):
        resolve_in_workspace(policy, "../data/scenarios.json")
