"""The pages closest to a lesson: what `gather_sources` adds to the lesson text (T18).

Cosine similarity between the lesson and every piece of page; a page scores as its best piece.
The pages found and their score go into the trace: when a sheet cites the wrong page, the trace
says what the search proposed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

import psycopg

from skoolathome.material.index import vector_literal

TOP_PAGES = 3
MIN_SCORE = 0.55  # below, a page is not about the lesson (to adjust with `material eval`, T31)


@dataclass(frozen=True)
class Hit:
    document_id: int
    title: str
    page: int
    score: float

    @property
    def key(self) -> str:
        """How a sheet cites the page: doc:12:p86."""
        return f"doc:{self.document_id}:p{self.page}"

    @property
    def label(self) -> str:
        return page_label(self.title, self.page)

    def as_json(self) -> dict:
        return {**asdict(self), "score": round(self.score, 3), "key": self.key, "label": self.label}


def page_label(title: str, page: int) -> str:
    """« ton manuel p. 86 » for a textbook, « « Fiche méthode » p. 2 » otherwise."""
    plain = unicodedata.normalize("NFD", title.lower())
    if re.search(r"\bmanuel\b", "".join(c for c in plain if unicodedata.category(c) != "Mn")):
        return f"ton manuel p. {page}"
    return f"« {title} » p. {page}"


def similar_pages(conn: psycopg.Connection, vector: list[float], subject_code: str | None,
                  limit: int = TOP_PAGES, min_score: float = 0.0) -> list[Hit]:
    """The `limit` best pages for this vector, among the documents of the subject and those
    without a subject."""
    rows = conn.execute(
        "SELECT c.document_id, d.title, c.page, 1 - (c.embedding <=> %s::vector) AS score "
        "FROM material.chunks c JOIN material.documents d ON d.id = c.document_id "
        "WHERE d.status = 'indexed' AND (d.subject_code IS NULL OR %s::text IS NULL OR d.subject_code = %s) "
        "ORDER BY c.embedding <=> %s::vector LIMIT %s",
        (vector_literal(vector), subject_code, subject_code, vector_literal(vector), limit * 8),
    ).fetchall()
    best: dict[tuple[int, int], Hit] = {}
    for document_id, title, page, score in rows:
        hit = Hit(document_id=document_id, title=title, page=page, score=float(score))
        if (document_id, page) not in best or hit.score > best[(document_id, page)].score:
            best[(document_id, page)] = hit
    ranked = sorted(best.values(), key=lambda h: -h.score)
    return [h for h in ranked if h.score >= min_score][:limit]


def page_text(conn: psycopg.Connection, document_id: int, page: int) -> str:
    row = conn.execute("SELECT text FROM material.pages WHERE document_id = %s AND number = %s",
                       (document_id, page)).fetchone()
    return row[0] if row else ""
