"""CLI: monitor | check-ab | report | demo | agent."""
from __future__ import annotations

import argparse
import json
import sys

from . import ab as ab_mod, monitor as mon_mod, reports, telegram
from .db import get_engine
from .policy import Policy


def cmd_monitor(args, policy, engine):
    res = mon_mod.run_monitor(engine, policy)
    dq_failed = [d for d in res["data_quality"] if d["status"] == "fail"]
    for m in res["metrics"]:
        if m["severity"] in ("warning", "critical"):
            telegram.send_alert(policy, f"metric:{m['metric']}", m["severity"], mon_mod.format_alert(m, dq_failed))
    for d in dq_failed:
        telegram.send_alert(policy, f"dq:{d['check']}", "warning", f"⚠️ Качество данных: {d['check']} — {d['detail']}")
    print(reports.monitor_report(res, args.audience))
    return res


def cmd_check_ab(args, policy, engine):
    res = ab_mod.check_ab_test(engine, args.experiment)
    print(reports.ab_report(res, args.audience))
    return res


def cmd_demo(args, policy, engine):
    """Сценарий без LLM: свежесть данных -> пайплайн -> мониторинг -> все A/B-тесты -> отчёт для PM."""
    from . import pipelines
    from .db import fetch_df
    print("=== 1. Качество данных ===")
    dq = mon_mod.check_data_quality(engine, policy)
    for d in dq:
        print(f"  {d['check']}: {d['status']} — {d.get('detail', '')}")
    if any(d["status"] == "fail" for d in dq):
        print("  -> витрины устарели, запускаю build_metrics")
        print("  ", pipelines.run_pipeline(policy, "build_metrics").get("output", "").strip())
    print("\n=== 2. Мониторинг ===")
    cmd_monitor(argparse.Namespace(audience="analyst"), policy, engine)
    print("\n=== 3. A/B-тесты ===")
    for e in fetch_df(engine, "SELECT experiment_id FROM experiments ORDER BY experiment_id")["experiment_id"]:
        r = ab_mod.check_ab_test(engine, e)
        print(f"  {e:<22} {r['verdict']:<20} {r['reason']}")
    print("\n=== 4. Отчёт для PM по exp_onboarding_v2 ===")
    print(reports.ab_report(ab_mod.check_ab_test(engine, "exp_onboarding_v2"), "pm"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pa_agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("monitor"); m.add_argument("--audience", default="analyst")
    c = sub.add_parser("check-ab"); c.add_argument("experiment"); c.add_argument("--audience", default="analyst")
    sub.add_parser("demo")
    a = sub.add_parser("agent"); a.add_argument("task"); a.add_argument("--model")
    args = ap.parse_args(argv)
    policy = Policy.load()
    if args.cmd == "agent":
        import asyncio
        from .agent import run_agent
        r = asyncio.run(run_agent(args.task, policy, model=args.model))
        if r.get("cost_usd") is not None:
            print(f"\n[ходов: {r['turns']}, стоимость: ${r['cost_usd']:.3f}]")
        return
    engine = get_engine()
    {"monitor": cmd_monitor, "check-ab": cmd_check_ab, "demo": cmd_demo}[args.cmd](args, policy, engine)


if __name__ == "__main__":
    main()
