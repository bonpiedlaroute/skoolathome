"""What this laptop can really do: tokens/s to read and to write, load time, memory (T14), and
the same for the other engines of the comparison (T30).

With Ollama, its own API reports durations (`prompt_eval_*`, `eval_*`, `load_duration`). Another
engine (llama.cpp, OpenVINO) is measured through the OpenAI-compatible API: the time to the first
token gives the reading speed, the rest the writing speed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from skoolathome import sysinfo

# A made-up lesson, close to what the generator will read: about 900 tokens of French.
SAMPLE_LESSON = """\
Chapitre 4 : les fractions.

1. Fractions égales. On ne change pas la valeur d'une fraction quand on multiplie ou qu'on divise
son numérateur et son dénominateur par un même nombre non nul. Par exemple, 3/4 = 6/8 = 9/12, et
12/18 = 2/3 en divisant le numérateur et le dénominateur par 6. Simplifier une fraction, c'est
l'écrire avec un numérateur et un dénominateur plus petits. Une fraction est irréductible quand on
ne peut plus la simplifier.

2. Comparer des fractions. Si deux fractions ont le même dénominateur, la plus grande est celle qui
a le plus grand numérateur : 5/7 > 3/7. Si les dénominateurs sont différents, on écrit d'abord les
deux fractions avec le même dénominateur. Pour comparer 2/3 et 3/5, on écrit 2/3 = 10/15 et
3/5 = 9/15, donc 2/3 > 3/5. Une fraction est plus grande que 1 quand son numérateur est plus grand
que son dénominateur.

3. Additionner et soustraire. Pour additionner deux fractions de même dénominateur, on additionne
les numérateurs et on garde le dénominateur : 2/9 + 5/9 = 7/9. Si les dénominateurs sont
différents, on les rend égaux d'abord : 1/4 + 1/6 = 3/12 + 2/12 = 5/12. On fait de même pour une
soustraction : 7/10 - 1/5 = 7/10 - 2/10 = 5/10 = 1/2.

4. Prendre une fraction d'une quantité. Prendre les 3/4 de 20, c'est calculer 20 divisé par 4,
puis multiplier par 3 : on obtient 15. On peut aussi multiplier d'abord : 20 × 3 = 60, puis
60 / 4 = 15. Exemple : dans une classe de 28 élèves, les 2/7 sont demi-pensionnaires, soit
28 / 7 × 2 = 8 élèves.

Exercices pour jeudi : page 86, exercices 12, 14 et 17. Revoir la méthode pour comparer deux
fractions de dénominateurs différents : il y aura une interrogation écrite mardi prochain.
"""

INSTRUCTION = ("Tu prépares des révisions pour un élève de 5e. À partir de ce cours, écris cinq "
               "questions avec leur réponse et une courte explication.\n\n")
GENERATED_TOKENS = 300


@dataclass(frozen=True)
class Measure:
    model: str
    load_seconds: float
    prompt_tokens: int
    prompt_tokens_per_second: float
    generated_tokens: int
    generated_tokens_per_second: float
    total_seconds: float
    model_bytes: int  # as reported by Ollama for the loaded model
    gpu_bytes: int  # part of it on the iGPU (Vulkan)
    ram_delta_bytes: int  # what the system lost in available memory while the model was loaded
    system_used_bytes: int = 0      # RAM used by the whole system, for engines that do not tell
    joules_per_token: float | None = None  # when the processor's energy counter can be read

    def row(self) -> str:
        if not self.model_bytes:  # another engine: it loaded the model at start, and does not say its size
            return (f"| `{self.model}` | {self.prompt_tokens_per_second:.0f} | {self.generated_tokens_per_second:.1f} | "
                    f"— | — | {sysinfo.gib(self.system_used_bytes)} utilisés au total | {self.total_seconds:.0f} s |")
        gpu = f"{sysinfo.gib(self.gpu_bytes)}" if self.gpu_bytes else "0"
        return (f"| `{self.model}` | {self.prompt_tokens_per_second:.0f} | "
                f"{self.generated_tokens_per_second:.1f} | {self.load_seconds:.1f} s | "
                f"{sysinfo.gib(self.model_bytes)} (dont iGPU {gpu}) | {sysinfo.gib(self.ram_delta_bytes)} | "
                f"{self.total_seconds:.0f} s |")


HEADER = ("| Modèle | Lecture (tokens/s) | Génération (tokens/s) | Chargement | Taille chargée | "
          "RAM prise | Durée d'un essai |\n|---|---|---|---|---|---|---|")


class Ollama:
    def __init__(self, native_url: str, timeout: float = 900) -> None:
        self.http = httpx.Client(base_url=native_url, timeout=timeout)

    def capabilities(self, model: str) -> list[str]:
        response = self.http.post("/api/show", json={"model": model})
        response.raise_for_status()
        return response.json().get("capabilities", [])

    def unload(self, model: str) -> None:
        self.http.post("/api/generate", json={"model": model, "keep_alive": 0}).raise_for_status()

    def unload_all(self, wait_seconds: float = 60) -> None:
        """Unload every model and wait until Ollama has let them go. `keep_alive: 0` answers before
        the memory is freed, and a model left from the previous run would hide this one's cost."""
        for entry in self.http.get("/api/ps").json().get("models", []):
            self.unload(entry["name"])
        deadline = time.monotonic() + wait_seconds
        while self.http.get("/api/ps").json().get("models"):
            if time.monotonic() > deadline:
                raise RuntimeError("Ollama n'a pas déchargé les modèles")
            time.sleep(0.5)
        time.sleep(1)  # let the runner process exit and the kernel take its memory back

    def loaded(self, model: str) -> dict:
        for entry in self.http.get("/api/ps").json().get("models", []):
            if entry.get("name") == model or entry.get("model") == model:
                return entry
        return {}

    def generate(self, model: str, prompt: str, think: bool | None) -> dict:
        body = {
            "model": model, "prompt": prompt, "stream": False, "keep_alive": "5m",
            "options": {"num_predict": GENERATED_TOKENS, "temperature": 0, "seed": 1},
        }
        if think is not None:
            body["think"] = think
        response = self.http.post("/api/generate", json=body)
        response.raise_for_status()
        return response.json()


def measure(ollama: Ollama, model: str, run: int = 1) -> Measure:
    """A cold start (model unloaded first) gives the load time; the speeds come from the same run.
    The run number goes first in the prompt, so that Ollama cannot reuse a cached prompt."""
    think = False if "thinking" in ollama.capabilities(model) else None  # reflection mode off
    ollama.unload_all()
    before = sysinfo.memory()
    energy = energy_joules()
    result = ollama.generate(model, f"Essai {run}.\n{INSTRUCTION}{SAMPLE_LESSON}", think)
    spent = _spent(energy, energy_joules())
    after = sysinfo.memory()
    loaded = ollama.loaded(model)
    seconds = 1e-9
    return Measure(
        model=model,
        load_seconds=result.get("load_duration", 0) * seconds,
        prompt_tokens=result.get("prompt_eval_count", 0),
        prompt_tokens_per_second=result.get("prompt_eval_count", 0) / max(result.get("prompt_eval_duration", 1) * seconds, 1e-9),
        generated_tokens=result.get("eval_count", 0),
        generated_tokens_per_second=result.get("eval_count", 0) / max(result.get("eval_duration", 1) * seconds, 1e-9),
        total_seconds=result.get("total_duration", 0) * seconds,
        model_bytes=loaded.get("size", 0),
        gpu_bytes=loaded.get("size_vram", 0),
        ram_delta_bytes=max(before.available - after.available, 0),
        system_used_bytes=after.used,
        joules_per_token=spent / result["eval_count"] if spent is not None and result.get("eval_count") else None,
    )


RAPL = Path("/sys/class/powercap/intel-rapl:0")  # the processor package: cores, iGPU and NPU


def energy_joules() -> float | None:
    """The processor's energy counter, or None: since 2020 most kernels let only root read it
    (sudo chmod o+r /sys/class/powercap/intel-rapl:0/energy_uj, until the next boot)."""
    try:
        return int((RAPL / "energy_uj").read_text()) / 1e6
    except (OSError, ValueError):
        return None


def _spent(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    if after < before:  # the counter wrapped around
        try:
            after += int((RAPL / "max_energy_range_uj").read_text()) / 1e6
        except (OSError, ValueError):
            return None
    return after - before


def measure_openai(base_url: str, model: str, run: int = 1, timeout: float = 900,
                   http: httpx.Client | None = None) -> Measure:
    """An OpenAI-compatible server (llama.cpp, OpenVINO), its model already loaded: streamed, the
    first token marks the end of the reading. The run number goes first in the prompt, so that the
    server cannot reuse a cached prompt."""
    http = http or httpx.Client(base_url=base_url, timeout=timeout)
    body = {"model": model, "messages": [{"role": "user", "content": f"Essai {run}.\n{INSTRUCTION}{SAMPLE_LESSON}"}],
            "max_tokens": GENERATED_TOKENS, "temperature": 0, "seed": 1, "stream": True,
            "stream_options": {"include_usage": True}}
    energy = energy_joules()
    started = time.monotonic()
    first: float | None = None
    usage: dict = {}
    pieces = 0
    with http.stream("POST", "/chat/completions", json=body) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content"):
                    first = first or time.monotonic()
                    pieces += 1
    ended = time.monotonic()
    first = first or ended
    generated = usage.get("completion_tokens") or pieces
    spent = _spent(energy, energy_joules())
    return Measure(
        model=model, load_seconds=0.0, prompt_tokens=usage.get("prompt_tokens", 0),
        prompt_tokens_per_second=usage.get("prompt_tokens", 0) / max(first - started, 1e-9),
        generated_tokens=generated, generated_tokens_per_second=generated / max(ended - first, 1e-9),
        total_seconds=ended - started, model_bytes=0, gpu_bytes=0, ram_delta_bytes=0,
        system_used_bytes=sysinfo.memory().used,
        joules_per_token=spent / generated if spent is not None and generated else None,
    )


def save(conn, engine: str, role: str, quantization: str, measured: Measure, release: str = "") -> None:
    conn.execute(
        "INSERT INTO agent.engine_benchmarks (engine, role, model, quantization, prompt_tokens_per_s, "
        "generated_tokens_per_s, load_seconds, model_bytes, system_used_bytes, joules_per_token, total_seconds, "
        "release) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (engine, role, measured.model, quantization, measured.prompt_tokens_per_second,
         measured.generated_tokens_per_second, measured.load_seconds or None, measured.model_bytes or None,
         measured.system_used_bytes or sysinfo.memory().used, measured.joules_per_token, measured.total_seconds,
         release),
    )
