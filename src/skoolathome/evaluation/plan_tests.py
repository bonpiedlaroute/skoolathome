"""The evening plan's tests (T19) in `make eval` (T31): the plan has no model, its cases are the
table tests of tests/test_plan.py. Run apart, they say whether a change of the rules or of the plan
still makes the right sheets on the right days; here they are one more family of the report.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FILES = ("tests/test_plan.py",)


def run(files: tuple[str, ...] = FILES) -> list[dict]:
    """Each test as a case: {case, measures: {plan_tests: 1 or 0}, output: {error}}."""
    with tempfile.TemporaryDirectory() as folder:
        report = Path(folder) / "plan.xml"
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}", *files],
            cwd=REPO, capture_output=True, text=True, timeout=600,
        )
        broken = [{"case": "plan", "origin": "test", "measures": {"plan_tests": 0.0},
                   "output": {"error": (done.stdout + done.stderr).strip()[-300:]}, "trace_id": None}]
        if done.returncode not in (0, 1) or not report.exists():  # not collected: a broken test file
            return broken
        results = []
        for test in ElementTree.parse(report).getroot().iter("testcase"):
            failure = test.find("failure")
            if failure is None:
                failure = test.find("error")
            if test.find("skipped") is not None:
                continue
            results.append({"case": test.get("name", ""), "origin": "test",
                            "measures": {"plan_tests": 0.0 if failure is not None else 1.0},
                            "output": {"error": (failure.get("message") or "")[:300]} if failure is not None else {},
                            "trace_id": None})
        return results or broken
