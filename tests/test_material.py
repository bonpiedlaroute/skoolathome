"""T18: the drop folder, text extraction (OCR when needed), the index and the page search."""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
from pathlib import Path

import pymupdf
import pytest

from skoolathome.collectors.manual.source import ManualSource, first_page, subject_of
from skoolathome.material import extract, index, search

FRACTIONS = ("Fractions égales. On ne change pas la valeur d'une fraction quand on multiplie le numérateur "
             "et le dénominateur par un même nombre non nul.")
COMPARE = ("Comparer des fractions. Si deux fractions ont le même dénominateur, la plus grande est celle "
           "qui a le plus grand numérateur.")
VOLCANO = ("Les volcans. Un volcan est une ouverture de la croûte terrestre par laquelle le magma remonte "
           "vers la surface et forme des coulées de lave.")


def write_pdf(path: Path, *pages: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), text, fontsize=11)
    document.save(path)
    return path


class FakeEmbedder:
    """Words hashed into 1024 dimensions: texts sharing words are close, as with a real model."""

    model = "fake-embedder"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []
        for text in texts:
            vector = [0.0] * 1024
            for word in re.findall(r"\w{4,}", text.lower()):
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 1024] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([x / norm for x in vector])
        return vectors


# -------------------------------------------------------------------- extraction


def test_text_pdf_is_read_page_by_page(tmp_path):
    pdf = write_pdf(tmp_path / "cours.pdf", FRACTIONS, COMPARE)
    got = extract.extract(pdf)
    assert not got.ocr and len(got.pages) == 2
    assert "numérateur" in got.pages[0] and "plus grande" in got.pages[1]


def test_a_scan_goes_through_ocr(tmp_path, monkeypatch):
    scan = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()  # no text layer
    document.save(scan)
    seen = []

    def fake_ocr(source: Path, target: Path) -> None:
        seen.append(source)
        write_pdf(target, FRACTIONS)

    monkeypatch.setattr(extract, "ocr", fake_ocr)
    got = extract.extract(scan)
    assert got.ocr and "numérateur" in got.pages[0] and seen == [scan]


def test_a_photo_becomes_a_pdf_then_goes_through_ocr(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), 0).save(photo)
    monkeypatch.setattr(extract, "ocr", lambda source, target: write_pdf(target, VOLCANO))
    got = extract.extract(photo)
    assert got.ocr and "magma" in got.pages[0]


def test_without_ocrmypdf_the_error_says_what_to_install(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ocrmypdf")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(extract.ExtractionError, match="apt install ocrmypdf tesseract-ocr-fra"):
        extract.ocr(tmp_path / "a.pdf", tmp_path / "b.pdf")
    with pytest.raises(extract.ExtractionError, match="format non traité"):
        extract.extract(tmp_path / "notes.docx")


# ------------------------------------------------------------------- drop folder


@pytest.mark.parametrize("folder, code", [("maths", "MATHS"), ("Histoire", "HI-GE"), ("Français", "FRANC"),
                                          ("SVT", "SVT"), ("divers", None)])
def test_the_folder_gives_the_subject(folder, code):
    assert subject_of(folder) == code


@pytest.mark.parametrize("name, page", [("manuel-maths-p84.pdf", 84), ("manuel_p.12.pdf", 12),
                                        ("chapitre 2 p 30.pdf", 30), ("chapitre-2.pdf", 1), ("map.pdf", 1)])
def test_the_name_gives_the_first_printed_page(name, page):
    assert first_page(name) == page


def test_the_manual_source_lists_and_reads_documents(tmp_path):
    write_pdf(tmp_path / "maths" / "manuel-maths-p84.pdf", FRACTIONS, COMPARE)
    (tmp_path / "notes.docx").write_bytes(b"not a pdf")
    source = ManualSource(tmp_path)
    refs = {ref.source_ref: ref for ref in source.list_documents()}
    manual = refs["maths/manuel-maths-p84.pdf"]
    assert (manual.subject_code, manual.first_page, manual.title) == ("MATHS", 84, "manuel maths p84")
    assert refs["notes.docx"].subject_code is None and not source.supported(refs["notes.docx"])
    pages = source.fetch(manual).pages
    assert [p.number for p in pages] == [84, 85] and not pages[0].ocr


# -------------------------------------------------------------------- index, search


def test_long_pages_are_cut_into_pieces():
    text = "\n\n".join(f"Paragraphe {n}. " + "mot " * 100 for n in range(6))
    pieces = index.chunks(text, size=1200)
    assert len(pieces) > 1 and all(len(p) <= 1200 for p in pieces)
    assert index.chunks("trop court") == []


def test_documents_are_indexed_once_and_follow_the_folder(conn, tmp_path):
    write_pdf(tmp_path / "maths" / "manuel-maths-p84.pdf", FRACTIONS, COMPARE)
    write_pdf(tmp_path / "svt" / "volcans.pdf", VOLCANO)
    (tmp_path / "notes.docx").write_bytes(b"x")
    source, embedder = ManualSource(tmp_path), FakeEmbedder()

    first = index.sync(conn, source, embedder)
    assert sorted(first.indexed) == ["maths/manuel-maths-p84.pdf", "svt/volcans.pdf"]
    assert first.unsupported == ["notes.docx"]
    assert conn.execute("SELECT count(*) FROM material.pages").fetchone()[0] == 3

    calls = embedder.calls
    again = index.sync(conn, source, embedder)
    assert again.indexed == [] and again.unchanged == 3 and embedder.calls == calls

    write_pdf(tmp_path / "svt" / "volcans.pdf", VOLCANO, "Les séismes. " + VOLCANO)  # the file changed
    (tmp_path / "maths" / "manuel-maths-p84.pdf").unlink()
    changed = index.sync(conn, source, embedder)
    assert changed.indexed == ["svt/volcans.pdf"] and changed.removed == ["maths/manuel-maths-p84.pdf"]
    assert conn.execute("SELECT count(*) FROM material.pages").fetchone()[0] == 2


def test_a_failed_document_is_reported_and_tried_again(conn, tmp_path, monkeypatch):
    scan = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(scan)

    def no_ocr(source: Path, target: Path) -> None:
        raise extract.ExtractionError("OCR impossible : installe-le avec sudo apt install ocrmypdf tesseract-ocr-fra")

    monkeypatch.setattr(extract, "ocr", no_ocr)
    result = index.sync(conn, ManualSource(tmp_path), FakeEmbedder())
    assert result.failed[0]["document"] == "scan.pdf" and "ocrmypdf" in result.failed[0]["error"]
    assert index.sync(conn, ManualSource(tmp_path), FakeEmbedder()).failed  # not forgotten


def test_the_right_page_comes_first_within_the_subject(conn, tmp_path):
    write_pdf(tmp_path / "maths" / "manuel-maths-p84.pdf", FRACTIONS, COMPARE)
    write_pdf(tmp_path / "svt" / "volcans.pdf", VOLCANO)
    embedder = FakeEmbedder()
    index.sync(conn, ManualSource(tmp_path), embedder)
    [query] = embedder.embed(["Aujourd'hui : comparer deux fractions qui ont le même dénominateur."])
    hits = search.similar_pages(conn, query, "MATHS")
    assert [(h.page, h.label) for h in hits[:2]] == [(85, "ton manuel p. 85"), (84, "ton manuel p. 84")]
    assert hits[0].score > hits[1].score and hits[0].key.endswith(":p85")
    assert all(h.title != "volcans" for h in hits)  # another subject
    assert search.page_text(conn, hits[0].document_id, 85).startswith("Comparer")
    assert search.similar_pages(conn, query, "MATHS", min_score=0.99) == []


def test_page_labels():
    assert search.page_label("Manuel maths p84", 86) == "ton manuel p. 86"
    assert search.page_label("Fiche méthode", 2) == "« Fiche méthode » p. 2"
