"""The shared correction vectors, run against the Python checker (the browser runs them too)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skoolathome.correction import check

VECTORS = [
    pytest.param(vector, id=f"{path.stem}: {vector['name']}")
    for path in sorted((Path(__file__).parent / "correction").glob("*.json"))
    for vector in json.loads(path.read_text())
]


@pytest.mark.parametrize("vector", VECTORS)
def test_vector(vector):
    result = check(
        vector["mode"], vector["answer"], vector["response"],
        variants=vector.get("variants"), tolerance=vector.get("tolerance", "0"),
    )
    assert result is vector["expected"]
