import pytest
from pa_agent.policy import ApprovalRequired, Policy, PolicyError, resolve_in_workspace, validate_select
from pa_agent.db import run_select
from pa_agent.files import write_file, read_file


@pytest.mark.parametrize("sql", [
    "DROP TABLE experiments", "DELETE FROM experiments", "UPDATE experiments SET name='x'",
    "SELECT 1; DROP TABLE experiments", "INSERT INTO experiments VALUES (1)",
    "PRAGMA writable_schema=1", "ATTACH DATABASE 'x.db' AS x", "SELECT load_extension('x')",
    "CREATE TABLE t AS SELECT 1", "", "   ",
])
def test_sql_guard_blocks(sql):
    with pytest.raises(PolicyError):
        validate_select(sql, "sqlite")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM experiments", "WITH a AS (SELECT 1 x) SELECT * FROM a",
    "SELECT 1 UNION SELECT 2",
])
def test_sql_guard_allows(sql):
    validate_select(sql, "sqlite")


def test_row_limit_truncates(engine, policy):
    policy.raw["sql"]["max_rows"] = 5
    r = run_select(engine, policy, "SELECT * FROM ab_assignments")
    assert r["n_rows"] == 5 and r["truncated"]


def test_db_is_read_only_even_if_guard_bypassed(engine):
    from sqlalchemy import text
    with engine.connect() as c:
        with pytest.raises(Exception):
            c.execute(text("DELETE FROM experiments"))
        with pytest.raises(Exception):
            c.execute(text("DROP TABLE users"))


def test_path_sandbox(policy):
    for bad in ["../evil.py", "/etc/passwd", "a/../../x.py", "run.sh", "x.exe"]:
        with pytest.raises(PolicyError):
            resolve_in_workspace(policy, bad)


def test_write_backup_and_diff(policy):
    r1 = write_file(policy, "q.sql", "select 1\n")
    r2 = write_file(policy, "q.sql", "select 2\n")
    assert r1["backup"] is None and r2["backup"] and "-select 1" in r2["diff"]
    assert read_file(policy, "q.sql") == "select 2\n"


def test_tiers(policy):
    policy.require("t0_read")
    with pytest.raises(ApprovalRequired):
        policy.require("t3_prod")
    policy.require("t3_prod", approved=True)
    policy.raw["autonomy"]["t3_prod"] = "deny"
    with pytest.raises(PolicyError):
        policy.require("t3_prod", approved=True)
