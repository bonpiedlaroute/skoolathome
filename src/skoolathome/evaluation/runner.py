"""`make eval` (ARCHITECTURE.md §14.3, T23): the graphs on the reference set, with a candidate
configuration, compared to the `production` one.

The candidate is the prompts labelled `staging` in Langfuse (each prompt without a staging
version is used in production), with the models of models.toml. Every run is recorded in
agent.eval_runs, and is a Langfuse experiment when Langfuse runs: two runs of a dataset can be
compared side by side in its interface. A prompt goes to `production` only after a run where
every blocking threshold held (`eval promote`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.agent import config, prompts, tracing
from skoolathome.evaluation import cases, plan_tests, retrieval
from skoolathome.evaluation.measures import (
    NAMES,
    THRESHOLDS,
    aggregate,
    detect_test,
    failed_thresholds,
    judge_sheet,
    make_sheet,
    measure_judge,
    measure_sheet,
    measure_test,
    prompts_of,
)

FAMILIES = ("sheets", "tests", "retrieval", "instructions", "judge", "plan")
FAMILY_NAMES = {"sheets": "fiches", "tests": "contrôles", "retrieval": "recherche", "instructions": "consignes",
                "judge": "correcteur", "plan": "plan du soir"}


@dataclass
class Run:
    id: int | None
    family: str
    label: str
    config: dict
    metrics: dict
    results: list[dict]
    passed: bool
    experiment: str = ""
    finished_at: datetime | None = None


def configuration(family: str, label: str, conf: config.Config | None = None) -> dict:
    """What the run measures: each prompt's version, the models, the engine, the code."""
    conf = conf or config.load()
    generator, judge = conf.model("generator"), conf.model("judge")
    embedder = conf.models.get("embedder")
    return {"prompts": {name: prompts.get(name, label).label for name in prompts_of(family)},
            "generator": f"{generator.model} {generator.quantization}", "judge": f"{judge.model} {judge.quantization}",
            "embedder": embedder.model if embedder else "", "engine": conf.engine.name, "release": tracing.release()}


def _replayable(family: str, case: cases.Case) -> bool:
    """A proposal refused or changed (T28) is kept in « consignes » but has no sentence to read again."""
    return family != "instructions" or bool(case.input.get("sentence"))


def _family(conn: psycopg.Connection, family: str, models: Any, loaded: dict, embedder: Any):
    """(task, measure) for one family: what the agent does with a case, and how it is judged."""
    if family == "tests":
        return (lambda case_input: detect_test(models, loaded[prompts_of("tests")[0]], case_input),
                lambda output, expected, origin: measure_test(output, expected))
    if family == "retrieval":
        if embedder is None:
            raise LookupError("modèle d'embedding absent : le jeu « recherche » attend (ollama pull bge-m3)")
        searcher, known = retrieval.Searcher(conn, embedder), retrieval.documents(conn)
        return (lambda case_input: retrieval.run_case(conn, searcher, case_input),
                lambda output, expected, origin: retrieval.measure(conn, output, expected, known))
    if family == "instructions":
        from datetime import date

        from skoolathome import summary
        from skoolathome.agent import evaluate_instructions as reading
        from skoolathome.agent.instructions import known as known_subjects
        from skoolathome.rules import store as rules_store

        subjects = known_subjects(conn, summary.latest_student(conn))
        existing = {rule.id: rule for rule in rules_store.latest(conn)}
        prompt = loaded[prompts_of("instructions")[0]]

        def understood(output: dict, expected: dict, origin: str) -> dict:
            output["problems"] = reading.problems(expected, output)  # kept with the result, shown by the report
            return {"instructions_correct": float(not output["problems"])}

        return (lambda case_input: reading.interpret(models, prompt, subjects, existing, case_input["sentence"],
                                                     date.today()), understood)
    if family == "judge":
        return (lambda case_input: judge_sheet(models, loaded[prompts_of("judge")[0]], case_input),
                lambda output, expected, origin: measure_judge(output, expected))
    return (lambda case_input: make_sheet(models, loaded.__getitem__, case_input),
            lambda output, expected, origin: measure_sheet(output, expected, origin))


def _plan(conn: psycopg.Connection, label: str, settings: dict) -> Run:
    """The plan's table tests, run as one more family (no model, no Langfuse dataset)."""
    started = datetime.now().astimezone()
    results = plan_tests.run()
    metrics = aggregate([r["measures"] for r in results])
    run = Run(None, "plan", label, settings, metrics, results, not failed_thresholds(metrics))
    run.id = _save(conn, run, started)
    return run


def _save(conn: psycopg.Connection, run: Run, started: datetime) -> int:
    return conn.execute(
        "INSERT INTO agent.eval_runs (family, label, config, started_at, finished_at, cases, metrics, results, passed, "
        "experiment) VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s) RETURNING id",
        (run.family, run.label, Jsonb(run.config), started, len(run.results), Jsonb(run.metrics), Jsonb(run.results),
         run.passed, run.experiment),
    ).fetchone()[0]


def evaluate(conn: psycopg.Connection, family: str, label: str, models: Any, *, limit: int | None = None,
             only: list[str] | None = None, progress: Callable[[str], None] | None = None,
             conf: config.Config | None = None, embedder: Any = None, tuning: bool = False) -> Run:
    say = progress or (lambda message: None)
    settings = configuration(family, label, conf)
    if family == "plan":
        return _plan(conn, label, settings)
    wanted = [c for c in cases.load(conn, family) if (not only or c.id in only) and _replayable(family, c)
              and not (tuning and c.metadata.get("holdout"))][:limit]
    if not wanted:
        raise LookupError(f"aucun cas « {FAMILY_NAMES[family]} » : skoolathome eval sync charge les cas écrits à la main")
    loaded = {name: prompts.get(name, label) for name in prompts_of(family)}
    started = datetime.now().astimezone()  # minutes per sheet, for the engine comparison (T30)
    task, measure = _family(conn, family, models, loaded, embedder)

    by_id = {case.id: case for case in wanted}
    lf = tracing.client()
    if lf is not None:
        cases.sync(conn)
    experiment = ""
    results: list[dict] = []
    if lf is not None and _dataset_ready(lf, family, by_id):
        experiment = f"{label} {settings['release']} {datetime.now():%Y-%m-%d %H:%M}"
        from langfuse import Evaluation

        def run_item(*, item, **kwargs):
            say(f"  {item.id}…")
            return task(item.input)

        def evaluator(*, output, expected_output, metadata=None, **kwargs):
            measures = measure(output, expected_output, (metadata or {}).get("origin", "real"))
            return [Evaluation(name=name, value=value) for name, value in measures.items() if value is not None]

        items = [item for item in lf.get_dataset(cases.DATASETS[family]).items if item.id in by_id]
        logging.getLogger("langfuse").setLevel(logging.ERROR)
        outcome = lf.run_experiment(
            name=f"skoolathome {FAMILY_NAMES[family]}", run_name=experiment, data=items, task=run_item,
            evaluators=[evaluator], max_concurrency=1,
            metadata={"label": label, **{f"prompt.{k}": v for k, v in settings["prompts"].items()},
                      "generator": settings["generator"], "judge": settings["judge"], "engine": settings["engine"],
                      "release": settings["release"]})
        for result in outcome.item_results:
            case = by_id[result.item.id]
            results.append(_result(case, result.output, measure(result.output, case.expected, case.origin),
                                   result.trace_id))
        tracing.flush()
    else:
        for case in wanted:
            say(f"  {case.id}…")
            output = task(case.input)
            results.append(_result(case, output, measure(output, case.expected, case.origin)))
    metrics = aggregate([r["measures"] for r in results])
    run = Run(None, family, label, settings, metrics, results, not failed_thresholds(metrics), experiment)
    run.id = _save(conn, run, started)
    return run


def _dataset_ready(lf: Any, family: str, wanted: dict) -> bool:
    try:
        found = {item.id for item in lf.get_dataset(cases.DATASETS[family]).items}
    except Exception:  # noqa: BLE001  (Langfuse stopped: the run goes on locally)
        return False
    return set(wanted) <= found


def _result(case: cases.Case, output: dict, measures: dict, trace_id: str | None = None) -> dict:
    """What is kept of a case: its measures, and enough of the output to understand a failure."""
    kept = {k: v for k, v in output.items() if k in ("status", "reason", "attempts", "detected", "by", "error", "hits",
                                                     "rule", "question", "verdict", "problems")}
    sheet = output.get("sheet")
    if sheet:
        kept["questions"] = [{"id": i["id"], "prompt": i["prompt"], "answer": i["answer"],
                              "source": (i.get("quote") or {}).get("source_id")} for i in sheet["items"]]
    return {"case": case.id, "origin": case.origin, "measures": measures, "output": kept, "trace_id": trace_id}


def latest(conn: psycopg.Connection, family: str, label: str, like: dict | None = None) -> Run | None:
    """The latest run; with `like`, the latest one with the same engine and models (a candidate
    prompt is compared with production on the same engine, T30)."""
    same = like or {}
    row = conn.execute(
        "SELECT id, family, label, config, metrics, results, passed, experiment, finished_at FROM agent.eval_runs "
        "WHERE family = %s AND label = %s AND finished_at IS NOT NULL "
        "AND (%s::text IS NULL OR (config->>'engine' = %s AND config->>'generator' = %s AND config->>'judge' = %s)) "
        "ORDER BY finished_at DESC LIMIT 1",
        (family, label, same.get("engine"), same.get("engine"), same.get("generator"), same.get("judge")),
    ).fetchone()
    return Run(*row) if row else None


def report(run: Run, baseline: Run | None) -> str:
    """The run against the production baseline: one line per measure, the blocking ones first."""
    used = ", ".join(f"{k} {v}" for k, v in run.config["prompts"].items())
    lines = [f"Jeu « {FAMILY_NAMES[run.family]} » : {len(run.results)} cas"
             + (f", consignes {run.label} ({used})" if used else "")
             + (f", modèle d'embedding {run.config.get('embedder')}" if run.family == "retrieval" else "")]
    if baseline is not None:
        lines.append(f"Comparé à production du {baseline.finished_at:%d/%m %H:%M} "
                     f"({', '.join(f'{k} {v}' for k, v in baseline.config['prompts'].items())})")
    else:
        lines.append("Pas encore de mesure de production pour comparer : make eval-production")
    lines.append(f"\n{'mesure':<32} {'production':>11} {'candidat':>10}  seuil")
    for name in [*THRESHOLDS, *(n for n in NAMES if n not in THRESHOLDS)]:
        now = run.metrics.get(name)
        before = (baseline.metrics if baseline else {}).get(name)
        if now is None and before is None:
            continue
        threshold = THRESHOLDS.get(name)
        state = "" if threshold is None or now is None else ("OK" if now["value"] >= threshold else "BLOQUANT")
        lines.append(f"{NAMES[name]:<32} {_shown(name, before):>11} {_shown(name, now):>10}  "
                     + (f"{threshold:.0%} {state}" if threshold is not None else "suivi"))
    failed = [r for r in run.results if any(v < 1.0 for k, v in r["measures"].items() if k in THRESHOLDS)]
    for result in failed[:10]:
        what = ", ".join(NAMES[k] for k, v in result["measures"].items() if k in THRESHOLDS and v < 1.0)
        output = result["output"]
        why = output.get("reason") or output.get("error") or "; ".join(output.get("problems") or [])
        if not why and output.get("hits") is not None:
            why = "trouvé : " + ", ".join(f"{h['label']} ({h['score']:.2f})" for h in output["hits"]) or "rien"
        lines.append(f"  ✗ {result['case']} : {what}" + (f" — {why[:140]}" if why else ""))
    lines.append("\n" + ("Tous les seuils bloquants sont tenus." if run.passed else
                         "Seuil bloquant non tenu : ce candidat ne passe pas en production."))
    return "\n".join(lines)


def _shown(name: str, metric: dict | None) -> str:
    if metric is None:
        return "—"
    value = metric["value"]
    return f"{value:.1f}" if name == "attempts" else f"{value:.0%} ({metric['cases']})"


def overview(runs: list[Run]) -> str:
    """One table for every family of the evening: what blocks, and why (T31)."""
    lines = ["\nmake eval : résumé", f"{'jeu':<14} {'cas':>4}  {'mesure bloquante':<32} {'valeur':>7}  {'seuil':>5}  état"]
    for run in runs:
        blocking = [name for name in THRESHOLDS if name in run.metrics]
        head = f"{FAMILY_NAMES[run.family]:<14} {len(run.results):>4}"
        if not blocking:
            lines.append(f"{head}  (suivi seulement)")
        for i, name in enumerate(blocking):
            value, minimum = run.metrics[name]["value"], THRESHOLDS[name]
            lines.append(f"{head if i == 0 else ' ' * 19}  {NAMES[name]:<32} {value:>7.0%}  {minimum:>5.0%}  "
                         + ("OK" if value >= minimum else "BLOQUANT"))
    passed = all(run.passed for run in runs)
    lines.append("\n" + ("Tous les seuils bloquants sont tenus." if passed else
                         "Au moins un seuil bloquant n'est pas tenu : rien ne passe en production."))
    return "\n".join(lines)


# --------------------------------------------------------------------------- promotion


def users(name: str) -> list[str]:
    """The families whose make eval measures this prompt: each of them must pass."""
    return [family for family in FAMILIES if name in prompts_of(family)]


def check_version(conn: psycopg.Connection, name: str, version: int) -> tuple[str, list[Run]]:
    """Why this version of the prompt cannot go to production ("" when it can), and the runs that
    say so: the latest staging run of every family using it, made with that very version, passed."""
    families = users(name)
    if not families:
        return "pas mesurée par make eval : aucun jeu ne l'utilise", []
    runs = []
    for family in families:
        run = latest(conn, family, "staging")
        if run is None or run.config["prompts"].get(name) != f"v{version}":
            return f"aucun make eval avec la version {version} (jeu « {FAMILY_NAMES[family]} »)", runs
        if not run.passed:
            return (f"make eval du {run.finished_at:%d/%m %H:%M} : seuil non tenu "
                    f"({', '.join(NAMES[m] for m in failed_thresholds(run.metrics))})"), runs
        runs.append(run)
    return "", runs


def evidence(runs: list[Run]) -> dict:
    """What the journal keeps of the runs behind a change (T32)."""
    return {"runs": [{"id": r.id, "family": r.family, "passed": r.passed, "finished_at": r.finished_at.isoformat(),
                      "metrics": {k: v["value"] for k, v in r.metrics.items() if k in THRESHOLDS}} for r in runs]}


@dataclass
class Promotion:
    moved: dict[str, tuple[int | None, int]]  # name -> (production before, version promoted)
    refused: dict[str, str]                  # name -> why


def promote(conn: psycopg.Connection, *, dry_run: bool = False, why: str = "") -> Promotion:
    """Each prompt whose `staging` version differs from `production` goes to production, only if
    the latest staging run of every family using it held every threshold with that very version.
    Each move is written in the journal of changes (T32)."""
    from skoolathome.evaluation import changes

    moved, refused = {}, {}
    for name in prompts.names():
        staging, production = prompts.label_version(name, "staging"), prompts.label_version(name, "production")
        if staging is None or staging == production or prompts.same_text(name, "staging", "production"):
            continue
        problem, runs = check_version(conn, name, staging)
        if problem:
            refused[name] = problem
            continue
        moved[name] = (production, staging)
        if not dry_run:
            changes.promoted(conn, name, production, staging, why, evidence(runs))
    return Promotion(moved, refused)
