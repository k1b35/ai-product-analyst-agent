"""Отчёты: одна и та же проверка, две подачи — для старшего аналитика (детали) и для PM (решение)."""
from __future__ import annotations

VERDICT_PM = {
    "valid_win": ("Раскатывать", "Эффект положительный и проверки пройдены."),
    "valid_loss": ("Не раскатывать", "Эффект отрицательный."),
    "valid_no_effect": ("Эффекта нет — решайте по стоимости", "Тест достаточно мощный, значимого эффекта нет."),
    "underpowered": ("Решения нет — нужно больше данных", "Выборки не хватило, «нет эффекта» утверждать нельзя."),
    "invalid": ("Результат использовать нельзя", "Нарушена корректность теста; нужен перезапуск после исправления."),
    "in_progress": ("Ждать", "Тест ещё идёт; смотреть результаты до конца запрещено (peeking)."),
    "guardrail_violation": ("Не раскатывать без обсуждения", "Основная метрика растёт, но ломается защитная."),
}
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌"}


def ab_report(res: dict, audience: str = "pm") -> str:
    e = res["effect"]
    if audience == "analyst":
        lines = [f"# A/B {res['experiment_id']} — {res['name']}",
                 f"**Вердикт:** `{res['verdict']}` — {res['reason']}", "",
                 f"Метрика: {res['primary_metric']} ({res['metric_type']}), n: {res['groups']}, alpha={res['alpha']}",
                 f"Эффект: {e['abs_diff']:+.4g} (отн. {(e['rel_diff'] or 0):+.1%}), "
                 f"95% ДИ [{e['ci_low']:+.4g}; {e['ci_high']:+.4g}], p={e['p_value']:.3g}, p(Holm)={e['p_value_holm']:.3g}", ""]
        if res.get("guardrail"):
            g = res["guardrail"]
            lines.append(f"Guardrail: {g['abs_diff']:+.4g}, p(Holm)={g['p_value_holm']:.3g}")
        lines += ["", "## Проверки"] + [f"- {ICON[c['status']]} **{c['name']}**: {c['detail']}" for c in res["checks"]]
        return "\n".join(lines)
    action, why = VERDICT_PM[res["verdict"]]
    rel = e["rel_diff"]
    lines = [f"**{res['name']}** → {action}", why]
    if res["verdict"] in ("valid_win", "valid_loss", "valid_no_effect", "guardrail_violation") and rel is not None:
        lines.append(f"Изменение метрики {res['primary_metric']}: {rel:+.1%} "
                     f"(доверительный интервал {e['ci_low']:+.3g} … {e['ci_high']:+.3g} в абсолютных единицах).")
    bad = [c for c in res["checks"] if c["status"] == "fail"]
    if bad:
        lines.append("Что не так: " + "; ".join(c["detail"] for c in bad))
    return "\n".join(lines)


def monitor_report(mon: dict, audience: str = "pm") -> str:
    out = []
    for m in mon["metrics"]:
        if m["severity"] in ("warning", "critical"):
            dd = (m.get("drill_down") or [{}])[0]
            seg = f" Главный источник: {dd['segment']} ({dd['pct']:+.0f}%)." if dd else ""
            if m.get("kind") == "trend":
                out.append(f"- {m['metric']}: плавное снижение, {m['trend_4w_pct']:+.1f}% за 4 недели (недельный уровень)."
                           + ("" if audience == "pm" else f" Сегодня к ожиданию {m['pct_vs_expected']:+.1f}%."))
                continue
            if audience == "analyst":
                out.append(f"- {m['metric']} {m['date']}: {m['value']:.4g} vs {m['expected']:.4g} "
                           f"({m['pct_vs_expected']:+.1f}%, z={m['robust_z']:.1f}), {m['severity']}.{seg}")
            else:
                out.append(f"- {m['metric']}: {m['pct_vs_expected']:+.0f}% к обычному уровню для этого дня недели.{seg}")
    for d in mon["data_quality"]:
        if d["status"] == "fail":
            out.append(f"- Данные: {d['check']} — {d['detail']}")
    return "\n".join(out) or "Отклонений нет."
