"""T30: the same model on several engines, measured the same way, in one table."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from skoolathome.agent import bench, config
from skoolathome.evaluation import engines, runner

ENGINES = Path(__file__).parent.parent / "src" / "skoolathome" / "agent" / "engines"


def test_each_engine_keeps_the_same_models_and_its_own_servers():
    ollama = config.load()
    for path in sorted(ENGINES.glob("*.toml")):
        conf = config.load(path)
        generator, judge, embedder = conf.model("generator"), conf.model("judge"), conf.model("embedder")
        assert generator.url(conf.engine) != judge.url(conf.engine), path.name  # one model per server
        assert embedder.metadata(conf.engine)["engine"] == "ollama"  # the vectors are not compared
        assert generator.metadata(conf.engine)["engine"] == conf.engine.name
        if path.name.startswith("llamacpp"):  # the same weights and quantization as with Ollama
            assert (generator.model, generator.quantization) == (ollama.model("generator").model, "Q4_K_M")


def test_an_openai_compatible_server_is_measured_from_its_stream():
    chunks = [{"choices": [{"delta": {"content": "Q1"}}]}, {"choices": [{"delta": {"content": " : 3/4"}}]},
              {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 300}}]
    sent = {}

    def answer(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    http = httpx.Client(base_url="http://engine/v1", transport=httpx.MockTransport(answer))
    measured = bench.measure_openai("http://engine/v1", "ministral", run=2, http=http)
    assert sent["stream"] and sent["max_tokens"] == bench.GENERATED_TOKENS and sent["messages"][0]["content"].startswith("Essai 2.")
    assert (measured.prompt_tokens, measured.generated_tokens) == (900, 300)
    assert measured.prompt_tokens_per_second > 0 and measured.generated_tokens_per_second > 0
    assert measured.system_used_bytes > 0 and "utilisés au total" in measured.row()


def run(conn, engine: str, family: str, metrics: dict, minutes: float = 0, cases: int = 1) -> None:
    finished = datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO agent.eval_runs (family, label, config, started_at, finished_at, cases, metrics, passed) "
        "VALUES (%s, 'production', %s, %s, %s, %s, %s, true)",
        (family, Jsonb({"engine": engine, "generator": "ministral Q4_K_M", "judge": "qwen Q4_K_M", "prompts": {}}),
         finished - timedelta(minutes=minutes), finished, cases, Jsonb(metrics)))


def test_the_table_puts_quality_and_speed_side_by_side(conn):
    run(conn, "ollama", "sheets", {"answers_correct": {"value": 1.0, "cases": 5}, "traps": {"value": 1.0, "cases": 4}},
        minutes=72, cases=9)
    run(conn, "llamacpp-vulkan", "sheets", {"answers_correct": {"value": 0.8, "cases": 5}}, minutes=36, cases=9)
    run(conn, "llamacpp-vulkan", "tests", {"test_recall": {"value": 1.0, "cases": 3}})
    for engine, writing in (("ollama", 3.4), ("llamacpp-vulkan", 6.8)):
        for role in ("generator", "judge"):
            bench.save(conn, engine, role, "Q4_K_M", bench.Measure(
                model="m", load_seconds=0, prompt_tokens=900, prompt_tokens_per_second=60.0, generated_tokens=300,
                generated_tokens_per_second=writing, total_seconds=100, model_bytes=0, gpu_bytes=0,
                ram_delta_bytes=0, system_used_bytes=14 * 2**30))
    text = engines.table(conn)
    rows = {line.split(" | ")[0].strip("| "): line for line in text.splitlines() if line.startswith("| ") and "Moteur" not in line}
    assert "| 80 % ✗ |" in rows["llamacpp-vulkan"] and "| 6.8 / 6.8 |" in rows["llamacpp-vulkan"]
    assert "| 100 % |" in rows["ollama"] and "| 8.0 |" in rows["ollama"]  # 72 min for 9 cases
    assert "| 4.0 |" in rows["llamacpp-vulkan"]


def test_a_candidate_is_compared_with_production_on_the_same_engine(conn):
    run(conn, "llamacpp-vulkan", "sheets", {})
    run(conn, "ollama", "sheets", {})
    like = {"engine": "llamacpp-vulkan", "generator": "ministral Q4_K_M", "judge": "qwen Q4_K_M"}
    assert runner.latest(conn, "sheets", "production", like=like).config["engine"] == "llamacpp-vulkan"
    assert runner.latest(conn, "sheets", "production", like={**like, "engine": "openvino-npu"}) is None
