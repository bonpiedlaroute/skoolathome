"""The sheet on paper: the formulas as text, every question type with room to answer."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from skoolathome.api import pdf
from skoolathome.schema.sheet import Sheet

SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "sheet-sample.json").read_text())


@pytest.mark.parametrize("latex, text", [
    (r"\frac{3}{4} + \dfrac{1}{2}", "3/4 + 1/2"),
    (r"\frac{a+b}{2}", "(a+b)/2"),
    (r"x^{2} \times 3^2 \leq \sqrt{16}", "x² × 3² ≤ √16"),
    (r"\widehat{ABC} = 45^\circ", "∠ABC = 45°"),
    (r"10^{-3}", "10⁻³"),
    (r"\text{aire} = \pi r^2", "aire = π r²"),
    (r"2^{n+k}", "2^(n+k)"),  # no superscript k: written plainly
])
def test_formulas_are_written_as_text(latex, text):
    assert pdf.formula(latex) == text


def test_every_question_type_prints_with_its_correction_apart():
    sheet = Sheet.model_validate(SAMPLE)
    document = pymupdf.open(stream=pdf.sheet_pdf(sheet), filetype="pdf")
    text = "".join(page.get_text() for page in document)
    assert sheet.title in text and "Prénom" in text and "Corrigé" in text
    assert "Corrigé" not in document[0].get_text() or document.page_count == 1
    for number in range(1, len(sheet.items) + 1):
        assert f"{number}." in text
    assert "$" not in text  # every formula written out
    assert pdf.file_name(sheet).endswith(".pdf") and pdf.file_name(sheet).isascii()
