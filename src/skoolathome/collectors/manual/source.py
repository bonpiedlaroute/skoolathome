"""The drop folder (ARCHITECTURE.md §5.5): PDFs exported from the tablet, handouts, photos.

    data/manual/
    ├── maths/manuel-maths-p84.pdf      → subject MATHS, the first page is printed page 84
    ├── histoire/chapitre-2.pdf         → subject HI-GE, pages numbered from 1
    └── fiche-methode.jpg               → any subject

The first folder gives the subject; « p84 » in the name gives the printed number of the first
page, so a sheet can cite « manuel p. 86 ».
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from skoolathome.material.extract import SUPPORTED_SUFFIXES, extract
from skoolathome.schema.material import CourseDocument, DocumentRef, Page
from skoolathome.subjects import code_of

DEFAULT_ROOT = Path("data/manual")
_FIRST_PAGE = re.compile(r"(?:^|[-_ .])p\.?\s?(\d{1,4})(?=$|[-_ .])", re.IGNORECASE)


def subject_of(folder: str) -> str | None:
    return code_of(folder)


def first_page(name: str) -> int:
    found = _FIRST_PAGE.search(Path(name).stem)
    return int(found.group(1)) if found else 1


class ManualSource:
    name = "manual"

    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = root

    def files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.rglob("*") if p.is_file() and not p.name.startswith("."))

    def ref(self, path: Path) -> DocumentRef:
        relative = path.relative_to(self.root)
        folder = relative.parts[0] if len(relative.parts) > 1 else ""
        return DocumentRef(
            source=self.name,
            source_ref=relative.as_posix(),
            title=re.sub(r"[-_]+", " ", path.stem).strip(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            subject_code=subject_of(folder) if folder else None,
            first_page=first_page(path.name),
        )

    def list_documents(self) -> list[DocumentRef]:
        return [self.ref(path) for path in self.files()]

    def supported(self, ref: DocumentRef) -> bool:
        return Path(ref.source_ref).suffix.lower() in SUPPORTED_SUFFIXES

    def fetch(self, ref: DocumentRef) -> CourseDocument:
        extracted = extract(self.root / ref.source_ref)
        pages = tuple(Page(number=ref.first_page + index, text=text, ocr=extracted.ocr)
                      for index, text in enumerate(extracted.pages))
        return CourseDocument(ref=ref, pages=pages)
