"""The engine comparison (ARCHITECTURE.md §10, T30): what the iGPU and the NPU gain over the
processor, with the same model, measured the same way.

Quality comes from `make eval` (agent.eval_runs, the thresholds of T23), speed from `llm bench
--save` (agent.engine_benchmarks). An engine is any configuration of SKOOLATHOME_MODELS: Ollama,
llama.cpp with Vulkan or on the processor, OpenVINO on the iGPU or the NPU.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg

from skoolathome import sysinfo
from skoolathome.evaluation.measures import THRESHOLDS

QUALITY = (("answers_correct", "Corrigés justes"), ("grounded", "Ancrage"), ("traps", "Pièges"),
           ("test_recall", "Contrôles"))


def _percent(metric: dict | None) -> str:
    if metric is None:
        return "—"
    mark = "" if metric["value"] >= THRESHOLDS.get(metric.get("name", ""), 0) else " ✗"
    return f"{round(metric['value'] * 100)} %{mark}"


def sheets_per_evening(conn: psycopg.Connection) -> float:
    """How many sheets an evening makes here, these two weeks: to turn minutes per sheet into an evening."""
    row = conn.execute(
        "SELECT avg(n) FROM (SELECT count(*) AS n FROM agent.sheets WHERE rule_id NOT IN ('', 'forced') "
        "AND run_date >= current_date - 14 GROUP BY run_date) t").fetchone()
    return float(row[0] or 0)


def collect(conn: psycopg.Connection) -> dict[str, dict]:
    """Per engine: the latest make eval of each family, and the latest speed of each role."""
    engines: dict[str, dict] = {}
    for family, config, metrics, started, finished, cases in conn.execute(
        "SELECT DISTINCT ON (config->>'engine', family) family, config, metrics, started_at, finished_at, cases "
        "FROM agent.eval_runs WHERE finished_at IS NOT NULL ORDER BY config->>'engine', family, finished_at DESC"
    ).fetchall():
        found = engines.setdefault(config.get("engine", "?"), {"quality": {}, "speed": {}})
        found["generator"] = config.get("generator", "")
        for name, metric in metrics.items():
            found["quality"][name] = {**metric, "name": name}
        if family == "sheets" and started and finished and cases:
            found["minutes_per_sheet"] = (finished - started) / timedelta(minutes=1) / cases
    for engine, role, model, quantization, reading, writing, used, joules in conn.execute(
        "SELECT DISTINCT ON (engine, role) engine, role, model, quantization, prompt_tokens_per_s, "
        "generated_tokens_per_s, system_used_bytes, joules_per_token FROM agent.engine_benchmarks "
        "ORDER BY engine, role, measured_at DESC"
    ).fetchall():
        found = engines.setdefault(engine, {"quality": {}, "speed": {}})
        found.setdefault("generator", f"{model} {quantization}" if role == "generator" else "")
        found["speed"][role] = {"reading": reading, "writing": writing, "used": used, "joules": joules}
    return engines


def table(conn: psycopg.Connection) -> str:
    engines = collect(conn)
    if not engines:
        return ("Rien à comparer : pour chaque moteur, lance llm bench --save et make eval-production avec son "
                "SKOOLATHOME_MODELS (voir tmp/T30-comparaison-moteurs.md).")
    evening = sheets_per_evening(conn)
    head = ["Moteur", "Générateur", *[name for _, name in QUALITY], "Lecture (tok/s)", "Écriture (tok/s, gén. / corr.)",
            "Min. par fiche", f"Soirée ({evening:.0f} fiches)" if evening else "Soirée", "RAM système", "Énergie (J/token)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for engine, found in sorted(engines.items()):
        generator = found["speed"].get("generator") or {}
        judge = found["speed"].get("judge") or {}
        minutes = found.get("minutes_per_sheet")
        cells = [
            engine, found.get("generator") or "—",
            *[_percent(found["quality"].get(key)) for key, _ in QUALITY],
            f"{generator['reading']:.0f}" if generator else "—",
            (f"{generator['writing']:.1f} / {judge['writing']:.1f}" if generator and judge else
             f"{generator['writing']:.1f}" if generator else "—"),
            f"{minutes:.1f}" if minutes else "—",
            f"{minutes * evening / 60:.1f} h" if minutes and evening else "—",
            sysinfo.gib(generator["used"]) if generator.get("used") else "—",
            f"{generator['joules']:.2f}" if generator.get("joules") else "—",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("\n✗ : seuil bloquant de make eval non tenu. La qualité vient du dernier make eval de chaque moteur "
                 "(mêmes consignes ?), la vitesse du dernier llm bench --save.")
    return "\n".join(lines)
