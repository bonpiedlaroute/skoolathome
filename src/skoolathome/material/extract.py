"""Text of a PDF or a photo, page by page, with OCR for the pages that have no text layer.

PyMuPDF reads the text layer. A scan or a photo has none: it goes through `ocrmypdf` with
Tesseract in French, installed on the laptop (sudo apt install ocrmypdf tesseract-ocr-fra).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SUPPORTED_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES
MIN_PAGE_CHARS = 30  # fewer characters: a scanned page, without a text layer
OCR_TIMEOUT_SECONDS = 600


class ExtractionError(Exception):
    """The file could not be read, or needs an OCR that is not installed."""


@dataclass(frozen=True)
class Extracted:
    pages: list[str]  # one text per page, in order
    ocr: bool


def pdf_pages(path: Path) -> list[str]:
    try:
        with pymupdf.open(path) as document:
            return [page.get_text("text") for page in document]
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ExtractionError(f"PDF illisible : {error}") from error


def needs_ocr(pages: list[str]) -> bool:
    """Most pages without text: a scan."""
    with_text = sum(1 for text in pages if len(text.strip()) >= MIN_PAGE_CHARS)
    return with_text * 2 < len(pages) or not pages


def image_to_pdf(image: Path, target: Path) -> None:
    with pymupdf.open(image) as picture:
        target.write_bytes(picture.convert_to_pdf())


def ocr(source: Path, target: Path) -> None:
    """Adds a text layer to the pages that have none (--skip-text keeps the others as they are)."""
    command = ["ocrmypdf", "-l", "fra", "--skip-text", "--rotate-pages", "--deskew", "--quiet",
               str(source), str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=OCR_TIMEOUT_SECONDS)
    except FileNotFoundError as error:
        raise ExtractionError("OCR impossible : installe-le avec sudo apt install ocrmypdf tesseract-ocr-fra") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip().splitlines()[-1:] if error.stderr else []
        raise ExtractionError(f"OCR en échec : {' '.join(detail) or error.returncode}") from error
    except subprocess.TimeoutExpired as error:
        raise ExtractionError("OCR trop long, abandonné") from error


def extract(path: Path) -> Extracted:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ExtractionError(f"format non traité : {suffix or 'sans extension'}")
    with tempfile.TemporaryDirectory(prefix="skoolathome-") as tmp:
        pdf = path
        if suffix in IMAGE_SUFFIXES:
            pdf = Path(tmp) / "image.pdf"
            image_to_pdf(path, pdf)
        pages = pdf_pages(pdf)
        if not needs_ocr(pages):
            return Extracted(pages=pages, ocr=False)
        with_text = Path(tmp) / "ocr.pdf"
        ocr(pdf, with_text)
        return Extracted(pages=pdf_pages(with_text), ocr=True)
