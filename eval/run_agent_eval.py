"""Прогон агента по кейсам eval/agent_cases.yaml на синтетическом мире.

    python eval/run_agent_eval.py --agent scripted         # эталон без LLM (проверка кейсов и грейдера)
    python eval/run_agent_eval.py --agent llm              # LLM-агент; нужен ANTHROPIC_API_KEY
    python eval/run_agent_eval.py --agent llm --cases srm_root_cause,peeking --model claude-sonnet-5-5

Каждый кейс идёт в чистой копии мира: свои state.db, audit.jsonl и копия БД (пайплайн её меняет).
Отчёт: eval/runs/<время>-<agent>/report.md + ответы и трассы вызовов по кейсам."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pa_agent.policy import Policy  # noqa: E402
from synth import default_db_path  # noqa: E402


def final_value(answer: str, prefix: str) -> str | None:
    """Итоговая строка «PREFIX: value» среди трёх последних непустых строк ответа."""
    for line in reversed([l.strip() for l in answer.strip().splitlines() if l.strip()][-3:]):
        m = re.match(rf"^\**{prefix}\**\s*[:：]\s*\**`?([^`*]+?)`?\**\s*$", line, re.I)
        if m:
            return m.group(1).strip().lower()
    return None


def grade(case: dict, answer: str, calls: list[dict], state_db: Path) -> dict:
    checks = []
    names = [c["tool"] for c in calls if c["status"] != "denied" or True]
    low = answer.lower()
    for t in case.get("must_call", []):
        checks.append((f"вызван {t}", t in names))
    for a, b in case.get("call_order", []):
        ia = [i for i, n in enumerate(names) if n == a]
        checks.append((f"{b} после {a}", bool(ia) and any(n == b for n in names[ia[0] + 1:])))
    if fl := case.get("final_line"):
        v = final_value(answer, fl["prefix"])
        checks.append((f"{fl['prefix']}: {'/'.join(fl['accept'])} (получено: {v})",
                       v is not None and any(v.startswith(x.lower()) for x in fl["accept"])))
    for group in case.get("must_mention", []):
        checks.append((f"упомянуто: {'|'.join(map(str, group))}", any(str(g).lower() in low for g in group)))
    for f in case.get("forbid_calls", []):
        bad = [c for c in calls if c["tool"] == f["tool"]
               and (not f.get("params_regex") or re.search(f["params_regex"], json.dumps(c["params"], ensure_ascii=False)))]
        checks.append((f"нет запрещённого вызова {f['tool']} {f.get('params_regex', '')}".strip(), not bad))
    st = case.get("state", {})
    if st:
        con = sqlite3.connect(state_db)
        if st.get("no_confirmed_hypotheses"):
            n = con.execute("SELECT COUNT(*) FROM hypotheses WHERE status IN ('confirmed','rejected')").fetchone()[0]
            checks.append(("гипотеза не переведена в confirmed/rejected без A/B", n == 0))
        if st.get("no_causal_hypotheses"):
            n = con.execute("SELECT COUNT(*) FROM hypotheses WHERE confidence='causal'").fetchone()[0]
            checks.append(("нет causal-уверенности без A/B", n == 0))
        if st.get("pending_approval"):
            n = con.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
            checks.append(("создан запрос на одобрение", n > 0))
        con.close()
    soft = [("желательно: " + "|".join(g), any(x.lower() in low for x in g)) for g in case.get("soft_mention", [])]
    return {"passed": all(ok for _, ok in checks), "checks": checks, "soft": soft}


def run_case(case: dict, agent: str, model: str | None, world: Path, run_dir: Path) -> dict:
    cdir = run_dir / case["id"]
    cdir.mkdir(parents=True)
    db = cdir / "world.db"
    shutil.copy(world, db)
    os.environ.update(PA_SQLITE_PATH=str(db), PA_STATE=str(cdir / "state.db"), PA_AUDIT=str(cdir / "audit.jsonl"))
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ.pop(k, None)                       # в eval алерты только в dry-run outbox
    from pa_agent.state import conn
    conn().close()
    policy = Policy.load()
    policy.raw["workspace"] = str(cdir / "workspace")
    t0, cost, err = time.time(), None, None
    try:
        if agent == "scripted":
            from eval.scripted_agent import solve
            from pa_agent.toolbox import Toolbox
            answer = solve(case["id"], Toolbox(policy))
        else:
            from pa_agent.agent import run_agent
            r = asyncio.run(run_agent(case["prompt"], policy, model=model, verbose=False))
            answer, cost = r["text"], r["cost_usd"]
    except Exception as e:  # noqa: BLE001
        answer, err = "", f"{type(e).__name__}: {e}"
    audit = cdir / "audit.jsonl"
    calls = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()] if audit.exists() else []
    g = grade(case, answer, calls, cdir / "state.db")
    if err:
        g["passed"] = False
        g["checks"].append((f"ошибка запуска: {err}", False))
    db.unlink()
    (cdir / "answer.md").write_text(answer, encoding="utf-8")
    return {"id": case["id"], "title": case["title"], **g, "seconds": round(time.time() - t0, 1), "cost_usd": cost,
            "n_calls": len(calls), "tools": [c["tool"] for c in calls]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["scripted", "llm"], default="scripted")
    ap.add_argument("--model")
    ap.add_argument("--cases", help="через запятую; по умолчанию все")
    ap.add_argument("--world", help="путь к БД мира; по умолчанию data/world.db")
    a = ap.parse_args()
    if a.agent == "llm" and not os.getenv("ANTHROPIC_API_KEY"):
        print("Нужен ANTHROPIC_API_KEY (console.anthropic.com → API Keys).", file=sys.stderr)
    world = Path(a.world or default_db_path())
    if not world.exists():
        sys.exit(f"Нет мира {world}. Сначала: python -m synth.generate")
    cases = yaml.safe_load((ROOT / "eval" / "agent_cases.yaml").read_text(encoding="utf-8"))
    if a.cases:
        keep = set(a.cases.split(","))
        cases = [c for c in cases if c["id"] in keep]
    run_dir = ROOT / "eval" / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{a.agent}"
    run_dir.mkdir(parents=True)
    results = []
    for c in cases:
        r = run_case(c, a.agent, a.model, world, run_dir)
        results.append(r)
        print(f"{'✅' if r['passed'] else '❌'} {r['id']:<24} {r['seconds']:>6}s  вызовов: {r['n_calls']}")
        for name, ok in r["checks"]:
            if not ok:
                print(f"     ✗ {name}")
    n_ok = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] or 0 for r in results)
    lines = [f"# Agent eval — {a.agent}{' / ' + a.model if a.model else ''}", "",
             f"Пройдено: **{n_ok}/{len(results)}**" + (f", стоимость ${cost:.2f}" if cost else ""), "",
             "| кейс | итог | вызовы инструментов | не прошло |", "|---|---|---|---|"]
    for r in results:
        failed = "; ".join(n for n, ok in r["checks"] if not ok) or "—"
        lines.append(f"| {r['title']} | {'✅' if r['passed'] else '❌'} | {', '.join(r['tools'])} | {failed} |")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПройдено {n_ok}/{len(results)}. Отчёт: {run_dir / 'report.md'}")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
