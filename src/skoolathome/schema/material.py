"""Course material in the common format (ARCHITECTURE.md §5.2): documents and their pages.

Whatever the source (the drop folder, École Directe attachments in T25), a document is a list
of pages with their printed number, so a sheet can cite « manuel p. 86 ».
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Material(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentRef(Material):
    source: str      # manual | ecoledirecte
    source_ref: str  # path under the drop folder, or the attachment id
    title: str
    sha256: str      # a changed file is indexed again
    subject_code: str | None = None  # None: may serve any subject
    first_page: int = Field(default=1, ge=0)  # printed number of the first page


class Page(Material):
    number: int  # printed page number
    text: str
    ocr: bool    # the text comes from OCR, not from the file


class CourseDocument(Material):
    ref: DocumentRef
    pages: tuple[Page, ...]
