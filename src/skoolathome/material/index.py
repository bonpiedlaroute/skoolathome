"""Index of course documents: pages kept with their printed number, cut into pieces, each with a
vector (pgvector). A new or changed file is indexed at the next evening run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import psycopg

from skoolathome.agent.embeddings import Embedder, EmbeddingError
from skoolathome.collectors.base import CourseMaterialSource
from skoolathome.material.extract import ExtractionError
from skoolathome.schema.material import CourseDocument, DocumentRef

CHUNK_CHARS = 1200  # about a paragraph or two: precise enough to find the right page
MIN_CHUNK_CHARS = 40


@dataclass
class IndexReport:
    indexed: list[str] = field(default_factory=list)
    unchanged: int = 0
    failed: list[dict] = field(default_factory=list)       # {"document": …, "error": …}
    unsupported: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"indexed": self.indexed, "unchanged": self.unchanged, "failed": self.failed,
                "unsupported": self.unsupported, "removed": self.removed}


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Pieces of about `size` characters, cut between paragraphs, then between sentences."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        parts = [paragraph] if len(paragraph) <= size else re.split(r"(?<=[.!?;:])\s+", paragraph)
        for part in parts:
            while len(part) > size:  # a single sentence longer than a piece
                pieces.append(part[:size])
                part = part[size:]
            if current and len(current) + len(part) + 1 > size:
                pieces.append(current)
                current = ""
            current = f"{current}\n{part}" if current else part
    if current:
        pieces.append(current)
    return [" ".join(p.split()) for p in pieces if len(p.strip()) >= MIN_CHUNK_CHARS]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def has_chunks(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM material.chunks)").fetchone()[0]


def _known(conn: psycopg.Connection, source: str) -> dict[str, tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT source_ref, sha256, status, embedding_model FROM material.documents WHERE source = %s", (source,)
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _upsert_document(conn: psycopg.Connection, ref: DocumentRef, *, status: str, error: str = "",
                     page_count: int = 0, ocr: bool = False, model: str = "") -> int:
    return conn.execute(
        "INSERT INTO material.documents (source, source_ref, title, subject_code, sha256, first_page, page_count, "
        "ocr, status, error, embedding_model) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (source, source_ref) DO UPDATE SET title = EXCLUDED.title, subject_code = EXCLUDED.subject_code, "
        "sha256 = EXCLUDED.sha256, first_page = EXCLUDED.first_page, page_count = EXCLUDED.page_count, "
        "ocr = EXCLUDED.ocr, status = EXCLUDED.status, error = EXCLUDED.error, "
        "embedding_model = EXCLUDED.embedding_model, indexed_at = now() RETURNING id",
        (ref.source, ref.source_ref, ref.title, ref.subject_code, ref.sha256, ref.first_page, page_count, ocr,
         status, error, model),
    ).fetchone()[0]


def store(conn: psycopg.Connection, document: CourseDocument, embedder: Embedder | None) -> int:
    """Replace the pages and pieces of a document. The vectors are computed before anything is
    written: a failure leaves the previous index as it was. Without an embedder, the pages only:
    a teacher's attachment is read with its lesson, no search needed (T25)."""
    pieces = [(page.number, position, text)
              for page in document.pages for position, text in enumerate(chunks(page.text))]
    if embedder is None:
        pieces = []
    vectors = embedder.embed([text for _, _, text in pieces]) if pieces else []
    with conn.transaction():
        doc_id = _upsert_document(conn, document.ref, status="indexed", page_count=len(document.pages),
                                  ocr=any(p.ocr for p in document.pages), model=embedder.model if embedder else "")
        conn.execute("DELETE FROM material.pages WHERE document_id = %s", (doc_id,))
        for page in document.pages:
            conn.execute("INSERT INTO material.pages (document_id, number, text, ocr) VALUES (%s, %s, %s, %s)",
                         (doc_id, page.number, page.text, page.ocr))
        for (number, position, text), vector in zip(pieces, vectors, strict=True):
            conn.execute(
                "INSERT INTO material.chunks (document_id, page, position, text, embedding) "
                "VALUES (%s, %s, %s, %s, %s::vector)",
                (doc_id, number, position, text, vector_literal(vector)),
            )
    return doc_id


def sync(conn: psycopg.Connection, source: CourseMaterialSource, embedder: Embedder | None) -> IndexReport:
    """Index what is new or changed in the source, forget what was removed from it. A document
    indexed without vectors gets them once the embedding model is there."""
    report = IndexReport()
    known = _known(conn, source.name)
    refs = source.list_documents()
    for ref in refs:
        previous = known.get(ref.source_ref)
        same_vectors = embedder is None or previous is not None and (
            previous[2] == embedder.model or previous[1] == "unsupported")
        if previous and previous[0] == ref.sha256 and previous[1] != "failed" and same_vectors:
            report.unchanged += 1
            continue
        supported = getattr(source, "supported", lambda _ref: True)(ref)
        if not supported:
            _upsert_document(conn, ref, status="unsupported", error="format non traité")
            report.unsupported.append(ref.source_ref)
            continue
        try:
            store(conn, source.fetch(ref), embedder)
            report.indexed.append(ref.source_ref)
        except (ExtractionError, EmbeddingError) as error:
            _upsert_document(conn, ref, status="failed", error=str(error)[:500])
            report.failed.append({"document": ref.source_ref, "error": str(error)[:300]})
    present = {ref.source_ref for ref in refs}
    for source_ref in sorted(set(known) - present):
        conn.execute("DELETE FROM material.documents WHERE source = %s AND source_ref = %s", (source.name, source_ref))
        report.removed.append(source_ref)
    return report
