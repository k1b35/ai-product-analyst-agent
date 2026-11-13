"""Статистический бенчмарк инструментов агента на синтетике с известной правдой: мониторинг + A/B-вердикты.
LLM не нужен. Запуск: python eval/run_eval.py [--worlds 40] [--ab-per-kind 25] [--seed 1]"""
from __future__ import annotations

import argparse, json, os, sqlite3, sys, tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synth.generate import generate  # noqa: E402
from pa_agent import ab, monitor  # noqa: E402
from pa_agent.db import get_engine  # noqa: E402
from pa_agent.policy import Policy  # noqa: E402


# ----------------------------- мониторинг -----------------------------------
SHARE = {"ios": 0.36, "android": 0.47, "web": 0.17}


def run_monitor_eval(n_worlds, seed, users=15000):
    """Случайные миры: падение платформы (или нет) x кампания блогеров (или нет). Правда известна заранее."""
    rng = np.random.default_rng(seed)
    pol = Policy.load()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n_worlds):
            pl = None if rng.random() < 0.3 else str(rng.choice(list(SHARE)))
            factor = float(rng.choice([0.95, 0.85, 0.7, 0.5])) if pl else 1.0
            campaign = bool(rng.random() < 0.5)
            p = generate(Path(tmp) / "w.db", users=users, seed=int(rng.integers(1e9)),
                         crash_platform=pl, crash_factor=factor, blogger_campaign=campaign, stale_days=0)
            eng = get_engine(f"sqlite:///file:{p}?mode=ro&uri=true")
            res = {m["metric"]: m for m in monitor.run_monitor(eng, pol)["metrics"]}
            eng.dispose()
            dau, conv = res["dau"], res["conversion_rate"]
            rows.append({"platform": pl, "factor": factor, "campaign": campaign,
                         "total_drop": SHARE.get(pl, 0) * (1 - factor),
                         "dau_alert": dau["severity"] != "ok" and dau.get("kind") == "spike",
                         "top": (dau.get("drill_down") or [{}])[0].get("segment"),
                         "conv_trend": conv["severity"] != "ok" and conv.get("kind") == "trend",
                         "any_alert": any(m["severity"] != "ok" for m in res.values())})
    return rows


# ------------------------------- A/B ----------------------------------------
KINDS = ["aa", "win", "srm", "underpowered", "running", "heavy_aa", "heavy_win"]
EXPECT = {"aa": {"valid_no_effect"}, "win": {"valid_win"}, "srm": {"invalid"}, "underpowered": {"underpowered", "valid_no_effect"},
          "running": {"in_progress"}, "heavy_aa": {"valid_no_effect"}, "heavy_win": {"valid_win"}}


def make_ab_db(rng, per_kind, path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE experiments(experiment_id TEXT, name TEXT, primary_metric TEXT, metric_type TEXT, start_date TEXT,
        end_date TEXT, status TEXT, planned_split TEXT, mde_abs REAL, planned_n_per_group INTEGER, alpha REAL);
    CREATE TABLE ab_assignments(experiment_id TEXT, user_id INTEGER, variant TEXT);
    CREATE TABLE ab_outcomes(experiment_id TEXT, user_id INTEGER, value REAL, pre_value REAL, guardrail REAL);""")
    truth, uid = {}, 0
    start = date.today() - timedelta(days=20)
    for kind in KINDS:
        for j in range(per_kind):
            eid = f"{kind}_{j}"; truth[eid] = kind
            heavy = kind.startswith("heavy")
            planned = 6000 if heavy else 3500
            n_c = n_t = {"underpowered": 700, "running": planned // 2}.get(kind, planned + 500)
            if kind == "srm":
                n_t = int(n_c * rng.uniform(0.86, 0.93))
            con.execute("INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (eid, eid, "m", "continuous" if heavy else "binary", start.isoformat(),
                 (start + timedelta(days=13)).isoformat(), "running" if kind == "running" else "finished",
                 json.dumps({"control": .5, "treatment": .5}), 3.0 if heavy else 0.02, planned, 0.05))
            A, O = [], []
            for var, n in (("control", n_c), ("treatment", n_t)):
                if heavy:
                    pre = rng.lognormal(1.0, 1.3, n)
                    y = 0.6 * pre + rng.lognormal(0.8, 1.6, n)
                    if kind == "heavy_win" and var == "treatment":
                        y = y * 1.25
                    guard = rng.normal(10, 3, n)
                else:
                    lift = {"win": .03, "srm": .03, "underpowered": .006, "running": .02}.get(kind, 0)
                    y = rng.binomial(1, .10 + (lift if var == "treatment" else 0), n).astype(float)
                    pre, guard = np.zeros(n), [None] * n
                for i in range(n):
                    uid += 1
                    A.append((eid, uid, var)); O.append((eid, uid, float(y[i]), float(pre[i]), guard[i]))
            con.executemany("INSERT INTO ab_assignments VALUES(?,?,?)", A)
            con.executemany("INSERT INTO ab_outcomes VALUES(?,?,?,?,?)", O)
    con.commit(); con.close()
    return truth


def run_ab_eval(per_kind, seed):
    rng = np.random.default_rng(seed)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ab.db"
        truth = make_ab_db(rng, per_kind, p)
        eng = get_engine(f"sqlite:///file:{p}?mode=ro&uri=true")
        return [(truth[e], ab.check_ab_test(eng, e)["verdict"]) for e in truth]


# ------------------------------- отчёт --------------------------------------
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--worlds", type=int, default=40)
    ap.add_argument("--ab-per-kind", type=int, default=25); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    out = ["# Результаты бенчмарка на синтетике\n"]

    m = run_monitor_eval(a.worlds, a.seed)
    pct = lambda xs: f"{np.mean(xs):.0%} ({sum(xs)}/{len(xs)})" if xs else "—"
    need = [r for r in m if r["total_drop"] >= 0.10]
    calm = [r for r in m if r["total_drop"] < 0.03]
    alerted = [r for r in m if r["dau_alert"] and r["platform"]]
    clean = [r for r in m if not r["platform"] and not r["campaign"]]
    out += ["## Мониторинг (синтетические миры с известной правдой)", f"- миров: {len(m)}", "",
            "| проверка | результат |", "|---|---|",
            f"| DAU: алерт при падении суммы ≥10% (recall) | {pct([r['dau_alert'] for r in need])} |",
            f"| DAU: ложный алерт, падения нет или <3% | {pct([r['dau_alert'] for r in calm])} |",
            f"| Drill-down указал верную платформу | {pct([r['top'] == r['platform'] for r in alerted])} |",
            f"| Конверсия: тренд найден, кампания блогеров идёт | {pct([r['conv_trend'] for r in m if r['campaign']])} |",
            f"| Конверсия: ложный тренд без кампании | {pct([r['conv_trend'] for r in m if not r['campaign']])} |",
            f"| Хоть один алерт в полностью «чистом» мире | {pct([r['any_alert'] for r in clean])} |"]
    by = defaultdict(list)
    for r in m:
        if r["platform"]:
            by[round(r["total_drop"], 3)].append(r["dau_alert"])
    out.append("\nRecall DAU по величине падения суммы: " +
               ", ".join(f"{k:.1%}→{np.mean(v):.0%}" for k, v in sorted(by.items())))

    ab_res = run_ab_eval(a.ab_per_kind, a.seed)
    ok = sum(v in EXPECT[k] for k, v in ab_res)
    out += ["", "## A/B-вердикты", f"- верных вердиктов: {ok}/{len(ab_res)} ({ok/len(ab_res):.0%})", "",
            "| правда | n | верно | распределение вердиктов |", "|---|---|---|---|"]
    for k in KINDS:
        vs = [v for kk, v in ab_res if kk == k]
        out.append(f"| {k} | {len(vs)} | {sum(v in EXPECT[k] for v in vs)/len(vs):.0%} | {dict(Counter(vs))} |")
    bad_wins = sum(v == "valid_win" for k, v in ab_res if k in ("aa", "heavy_aa", "srm", "underpowered", "running"))
    bad_neg = sum(v in ("valid_loss", "guardrail_violation") for k, v in ab_res if k in ("aa", "heavy_aa"))
    out += ["", f"- **ложный «win» там, где его быть не должно (A/A, SRM, мало данных, идёт): {bad_wins}**",
            f"- ложные loss/guardrail на A/A: {bad_neg}"]
    text = "\n".join(out); print(text)
    Path(__file__).with_name("RESULTS.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
