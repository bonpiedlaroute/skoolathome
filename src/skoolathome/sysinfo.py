"""Memory and swap, read from /proc: for the diagnostic (T14) and the evening report (T17)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")


@dataclass(frozen=True)
class Memory:
    total: int  # bytes
    available: int
    swap_total: int
    swap_free: int

    @property
    def used(self) -> int:
        return self.total - self.available

    @property
    def swap_used(self) -> int:
        return self.swap_total - self.swap_free

    def describe(self) -> str:
        return (f"RAM {gib(self.used)} utilisés sur {gib(self.total)}, swap {gib(self.swap_used)} "
                f"sur {gib(self.swap_total)}")


@dataclass(frozen=True)
class SwapUser:
    pid: int
    name: str
    swap: int  # bytes
    rss: int


def gib(value: int) -> str:
    return f"{value / 2**30:.1f} Go"


def memory(proc: Path = PROC) -> Memory:
    fields = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        fields[key] = int(rest.split()[0]) * 1024  # the file counts in kB
    return Memory(fields["MemTotal"], fields["MemAvailable"], fields["SwapTotal"], fields["SwapFree"])


def swap_users(proc: Path = PROC, limit: int = 15) -> list[SwapUser]:
    """The processes with the most memory in swap. Only yours are readable without root."""
    users = []
    for status in proc.glob("[0-9]*/status"):
        try:
            values = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
        except OSError:  # gone, or not readable
            continue
        swap = int(values.get("VmSwap", "0 kB").split()[0]) * 1024
        if swap:
            rss = int(values.get("VmRSS", "0 kB").split()[0]) * 1024
            users.append(SwapUser(int(status.parent.name), values["Name"].strip(), swap, rss))
    return sorted(users, key=lambda u: u.swap, reverse=True)[:limit]


def by_program(users: list[SwapUser]) -> list[tuple[str, int, int]]:
    """(name, number of processes, swap) per program: twenty Chrome processes are one line."""
    totals: dict[str, list[int]] = {}
    for user in users:
        entry = totals.setdefault(user.name, [0, 0])
        entry[0] += 1
        entry[1] += user.swap
    return sorted(((name, n, swap) for name, (n, swap) in totals.items()), key=lambda t: t[2], reverse=True)
