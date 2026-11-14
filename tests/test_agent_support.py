"""T14: local prompt copies, model configuration, memory readings."""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from skoolathome import sysinfo
from skoolathome.agent import bench, config, llm, prompts
from skoolathome.agent.draft import DraftSheet


def test_every_local_prompt_has_a_system_and_a_user_message():
    assert set(prompts.names()) >= {"lesson-summary", "sheet-generator"}
    for name in prompts.names():
        roles = [m["role"] for m in prompts.local_messages(name)]
        assert roles == ["system", "user"], name


def test_compile_fills_every_variable_and_refuses_a_missing_one():
    prompt = prompts.Prompt("lesson-summary", None, prompts.local_messages("lesson-summary"))
    messages = prompt.compile(subject="Français", day="12/11/2026", content="Yvain.")
    assert "Matière : Français" in messages[1]["content"] and "{{" not in messages[1]["content"]
    assert prompt.label == "local"
    with pytest.raises(KeyError):
        prompt.compile(subject="Français")


def test_without_langfuse_the_local_copy_is_used():
    found = prompts.get("sheet-generator")  # tests run without Langfuse (conftest)
    assert found.version is None and found.client is None
    assert found.messages == prompts.local_messages("sheet-generator")


def test_models_come_from_two_families():
    conf = config.load()
    generator, judge = conf.model("generator"), conf.model("judge")
    assert generator.family != judge.family
    assert judge.reasoning_effort == "none"
    assert generator.metadata(conf.engine) == {
        "model": generator.model, "family": generator.family, "quantization": "Q4_K_M", "engine": "ollama"}


def test_a_model_that_does_not_answer_is_a_model_error():
    """A timeout or a stopped Ollama must reach the graph as a failed attempt, not stop the evening."""
    conf = config.load()
    unreachable = dataclasses.replace(conf, engine=dataclasses.replace(conf.engine, base_url="http://127.0.0.1:9/v1"))
    models = llm.Models(unreachable)
    messages = [{"role": "user", "content": "Bonjour"}]
    with pytest.raises(llm.ModelError, match="pas de réponse"):
        models.text("generator", messages, [])
    with pytest.raises(llm.ModelError, match="pas de réponse"):
        models.structured("generator", messages, DraftSheet, [])


def test_bench_unloads_every_model_and_waits_until_they_are_gone(monkeypatch):
    unloaded, polls = [], []

    def ollama_api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            polls.append(request)
            still_there = len(polls) <= 2  # Ollama answers the unload before letting the models go
            return httpx.Response(200, json={"models": [{"name": "ministral"}, {"name": "qwen"}] if still_there else []})
        unloaded.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={})

    ollama = bench.Ollama("http://ollama")
    ollama.http = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(ollama_api))
    monkeypatch.setattr(bench.time, "sleep", lambda seconds: None)
    ollama.unload_all()
    assert unloaded == ["ministral", "qwen"] and len(polls) == 3


def test_memory_and_swap_users(tmp_path):
    (tmp_path / "meminfo").write_text(
        "MemTotal:       32000000 kB\nMemFree: 1 kB\nMemAvailable:   16000000 kB\n"
        "SwapTotal:       4000000 kB\nSwapFree:        1000000 kB\n")
    for pid, name, swap in [(10, "chrome", 500), (11, "chrome", 700), (12, "code", 300), (13, "bash", 0)]:
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "status").write_text(f"Name:\t{name}\nVmRSS:\t   100 kB\nVmSwap:\t   {swap} kB\n")
    memory = sysinfo.memory(tmp_path)
    assert memory.used == 16000000 * 1024 and memory.swap_used == 3000000 * 1024
    users = sysinfo.swap_users(tmp_path)
    assert [u.pid for u in users] == [11, 10, 12]
    assert sysinfo.by_program(users) == [("chrome", 2, 1200 * 1024), ("code", 1, 300 * 1024)]
