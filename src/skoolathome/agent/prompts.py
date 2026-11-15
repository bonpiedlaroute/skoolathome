"""Model prompts: managed in Langfuse (label `production`), with a local copy in prompts/*.md.

The local copy is the fallback when Langfuse is stopped, and the source of `skoolathome prompt
push`. Every trace records which version was used: a Langfuse version number, or "local".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from skoolathome.agent import tracing

PROMPTS_DIR = Path(__file__).parent / "prompts"
ROLE_MARK = re.compile(r"^<!-- role: (system|user|assistant) -->[ \t]*$", re.MULTILINE)
VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def local_messages(name: str) -> list[dict[str, str]]:
    """prompts/<name>.md: messages introduced by <!-- role: system --> or <!-- role: user -->."""
    parts = ROLE_MARK.split((PROMPTS_DIR / f"{name}.md").read_text())
    return [{"role": role, "content": content.strip()} for role, content in zip(parts[1::2], parts[2::2])]


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int | None  # None: the local copy
    messages: list[dict[str, str]]
    client: object | None = None  # the Langfuse prompt, linked to the generations it produced

    @property
    def label(self) -> str:
        return f"v{self.version}" if self.version is not None else "local"

    def compile(self, **variables: object) -> list[dict[str, str]]:
        """Fill the {{variables}}. A missing variable is an error, not an empty string."""
        def fill(match: re.Match) -> str:
            return str(variables[match.group(1)])

        return [{"role": m["role"], "content": VARIABLE.sub(fill, m["content"])} for m in self.messages]


def get(name: str, label: str = "production") -> Prompt:
    """The version with `label`. A prompt without a `staging` version is used in its `production`
    version: a candidate (T23) only needs the prompts it changes."""
    found = _get(name, label)
    if found.version is None and label != "production":
        return _get(name, "production")
    return found


def _get(name: str, label: str) -> Prompt:
    fallback = local_messages(name)
    lf = tracing.client()
    if lf is None:
        return Prompt(name, None, fallback)
    logger = logging.getLogger("langfuse")
    level = logger.level
    logger.setLevel(logging.ERROR)  # a prompt not pushed yet is not an incident: the trace says "local"
    try:
        found = lf.get_prompt(name, label=label, type="chat", fallback=fallback, fetch_timeout_seconds=5, max_retries=1)
    except Exception:  # Langfuse stopped or unreachable: the evening goes on with the local copy
        return Prompt(name, None, fallback)
    finally:
        logger.setLevel(level)
    if found.is_fallback:
        return Prompt(name, None, fallback)
    messages = [{"role": m["role"], "content": m["content"]} for m in found.prompt]
    return Prompt(name, found.version, messages, found)


def push(name: str, label: str = "production") -> tuple[int, bool]:
    """Send the local copy to Langfuse as a new version with `label`, unless it is already the one labelled.
    Returns (version, created)."""
    lf = tracing.client()
    if lf is None:
        raise RuntimeError("Langfuse non configuré : deploy/langfuse/langfuse.sh init, puis start")
    messages = local_messages(name)
    try:
        current = lf.get_prompt(name, label=label, type="chat", cache_ttl_seconds=0, max_retries=0)
        if [{"role": m["role"], "content": m["content"]} for m in current.prompt] == messages:
            return current.version, False
    except Exception:  # first version of this prompt
        pass
    created = lf.create_prompt(
        name=name, prompt=messages, type="chat", labels=[label], commit_message=f"git {tracing.release()}"
    )
    return created.version, True


def names() -> list[str]:
    return sorted(path.stem for path in PROMPTS_DIR.glob("*.md"))


def label_version(name: str, label: str) -> int | None:
    """The version carrying `label` in Langfuse, or None."""
    found = _get(name, label)
    return found.version


def same_text(name: str, label: str, other: str) -> bool:
    """Two labels on the same messages, even as two versions."""
    return _get(name, label).messages == _get(name, other).messages


def move_label(name: str, version: int, label: str = "production") -> None:
    """Put `label` on that version: Langfuse takes it off the version that had it (T23, `eval promote`)."""
    lf = tracing.client()
    if lf is None:
        raise RuntimeError("Langfuse non configuré")
    current = lf.get_prompt(name, version=version, type="chat", cache_ttl_seconds=0, max_retries=0)
    labels = sorted(set(current.labels or []) - {"latest"} | {label})
    lf.api.prompt_version.update(name, version, new_labels=labels)
