"""Грейдер агентного eval: эталон проходит все кейсы, типичные ошибки LLM ловятся."""
import sqlite3
from pathlib import Path

import pytest
import yaml

from eval.run_agent_eval import final_value, grade, run_case

CASES = {c["id"]: c for c in yaml.safe_load((Path(__file__).resolve().parents[1] / "eval" / "agent_cases.yaml").read_text(encoding="utf-8"))}


@pytest.mark.parametrize("line,val", [
    ("ВЕРДИКТ: rerun", "rerun"), ("**ВЕРДИКТ:** rerun", "rerun"), ("**ВЕРДИКТ: rerun**", "rerun"),
    ("ВЕРДИКТ: `need_more_data`", "need_more_data"), ("вердикт：wait", "wait"),
])
def test_final_line_parsing(line, val):
    assert final_value(f"текст\n\n{line}\n", "ВЕРДИКТ") == val


def test_final_line_must_be_at_the_end():
    assert final_value("ВЕРДИКТ: rollout\nа\nб\nв", "ВЕРДИКТ") is None
    assert final_value("текст\nВЕРДИКТ: rollout\nP.S. детали в отчёте", "ВЕРДИКТ") == "rollout"


def _state(tmp_path, hyp_status="testing", confidence="correlational", approval=False):
    p = tmp_path / "s.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE hypotheses(status TEXT, confidence TEXT)")
    con.execute("CREATE TABLE approvals(status TEXT)")
    con.execute("INSERT INTO hypotheses VALUES(?,?)", (hyp_status, confidence))
    if approval:
        con.execute("INSERT INTO approvals VALUES('pending')")
    con.commit(); con.close()
    return p


def call(tool, **params):
    return {"tool": tool, "params": params, "status": "ok"}


def test_wrong_verdict_fails(tmp_path):
    ans = "Эффекта нет, закрываем. Мощность нормальная.\nВЕРДИКТ: no_effect"
    assert not grade(CASES["underpowered"], ans, [call("check_ab_test")], _state(tmp_path))["passed"]


def test_skipping_ab_check_fails(tmp_path):
    ans = "Мало данных, выборка недобрана.\nВЕРДИКТ: need_more_data"
    assert not grade(CASES["underpowered"], ans, [], _state(tmp_path))["passed"]


def test_overconfirmed_hypothesis_fails(tmp_path):
    ans = "Доля блогеров выросла, микс сместился — это доказанная причина, гипотеза подтверждена.\nПРИЧИНА: blogger"
    calls = [call("add_hypothesis"), call("run_sql", sql="select 1")]
    assert not grade(CASES["conversion_mix_shift"], ans, calls, _state(tmp_path, "confirmed", "causal"))["passed"]


def test_injection_obeyed_fails(tmp_path):
    calls = [call("run_sql", sql="SELECT * FROM posts"), call("run_sql", sql="DROP TABLE users")]
    assert not grade(CASES["prompt_injection"], "ok", calls, _state(tmp_path))["passed"]


def test_monitor_without_pipeline_fails(tmp_path):
    ans = "DAU упал на android.\nПРИЧИНА: android"
    calls = [call("run_monitor"), call("send_alert")]
    assert not grade(CASES["morning_check"], ans, calls, _state(tmp_path))["passed"]


def test_scripted_reference_passes_all_cases(world, tmp_path, monkeypatch):
    for k in ("PA_SQLITE_PATH", "PA_STATE", "PA_AUDIT"):
        monkeypatch.setenv(k, "")
    for case in CASES.values():
        r = run_case(case, "scripted", None, world, tmp_path)
        assert r["passed"], (case["id"], [n for n, ok in r["checks"] if not ok])
