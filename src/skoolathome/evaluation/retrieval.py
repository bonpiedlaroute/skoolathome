"""The « recherche » set in `make eval` (T31): for a lesson, is the expected page among the first
three the search returns (recall@3)? A page well quoted but off the subject is a sheet the citation
guard lets through: the search is judged on its own.

The candidate embedding model is the one of models.toml. When it is not the model the documents
were indexed with, the pages of the subject are embedded again in memory with the candidate, so a
change of model is measured before any reindexing: a bad model is blocked, the index untouched.
"""

from __future__ import annotations

import math
from typing import Any

import psycopg

from skoolathome.material import search

TOP = search.TOP_PAGES


def query(conn: psycopg.Connection, lesson_id: str | None, text: str | None) -> tuple[str, str | None]:
    """(text to search with, subject) for a lesson id, or a free text. An empty lesson searches with
    the homework given that day (« apprendre la leçon p. 86 »), as the evening does."""
    if text:
        return text, None
    row = conn.execute("SELECT content, subject_code, day FROM school.lessons WHERE source_id = %s",
                       (lesson_id,)).fetchone()
    if row is None:
        raise LookupError(f"séance inconnue : {lesson_id}")
    content, subject, day = row
    if not content.strip():
        homework = conn.execute("SELECT string_agg(text, ' ') FROM school.homework WHERE subject_code = %s "
                                "AND given_on = %s", (subject, day)).fetchone()[0]
        content = homework or ""
    return content, subject


def documents(conn: psycopg.Connection) -> dict[int, tuple[str, str]]:
    return {row[0]: (row[1].lower(), row[2].lower()) for row in conn.execute(
        "SELECT id, source_ref, title FROM material.documents").fetchall()}


def matches(expected: dict, hit: dict, known: dict[int, tuple[str, str]]) -> bool:
    """The right document (its title, path or file fingerprint), and the right page when one is
    expected: an attachment is right at any page."""
    source_ref, title = known.get(hit["document_id"], ("", ""))
    if expected.get("page") is not None and expected["page"] != hit["page"]:
        return False
    if expected.get("source_ref"):
        return source_ref == expected["source_ref"].lower()
    return expected.get("document", "").lower() in (source_ref, title)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class Searcher:
    """The search as the evening runs it, with the embedding model of the configuration."""

    def __init__(self, conn: psycopg.Connection, embedder: Any) -> None:
        self.conn, self.embedder = conn, embedder
        indexed = {row[0] for row in conn.execute(
            "SELECT DISTINCT embedding_model FROM material.documents WHERE status = 'indexed'").fetchall()}
        self.in_memory = bool(indexed) and indexed != {embedder.model}
        self.indexed = sorted(indexed)
        self._pieces: dict[str | None, list[tuple[int, str, int, list[float]]]] = {}

    def _subject_pieces(self, subject: str | None) -> list[tuple[int, str, int, list[float]]]:
        """Every piece of page of the subject (and of the documents without one), embedded once
        with the candidate model."""
        if subject not in self._pieces:
            rows = self.conn.execute(
                "SELECT c.document_id, d.title, c.page, c.text FROM material.chunks c "
                "JOIN material.documents d ON d.id = c.document_id WHERE d.status = 'indexed' "
                "AND (d.subject_code IS NULL OR %s::text IS NULL OR d.subject_code = %s) ORDER BY c.id",
                (subject, subject),
            ).fetchall()
            vectors = self.embedder.embed([r[3] for r in rows]) if rows else []
            self._pieces[subject] = [(r[0], r[1], r[2], v) for r, v in zip(rows, vectors, strict=True)]
        return self._pieces[subject]

    def top(self, text: str, subject: str | None, limit: int = TOP) -> list[dict]:
        vector = self.embedder.embed([text])[0]
        if not self.in_memory:
            return [hit.as_json() for hit in search.similar_pages(self.conn, vector, subject, limit=limit)]
        best: dict[tuple[int, int], dict] = {}
        for document_id, title, page, piece in self._subject_pieces(subject):
            score = _cosine(vector, piece)
            if (document_id, page) not in best or score > best[(document_id, page)]["score"]:
                best[(document_id, page)] = {"document_id": document_id, "title": title, "page": page,
                                             "score": round(score, 3), "label": search.page_label(title, page)}
        return sorted(best.values(), key=lambda h: -h["score"])[:limit]


def run_case(conn: psycopg.Connection, searcher: Searcher, case_input: dict) -> dict:
    try:
        text, subject = query(conn, case_input.get("lesson"), case_input.get("query"))
    except LookupError as error:
        return {"hits": [], "error": str(error)}
    if not text.strip():
        return {"hits": [], "error": "rien à chercher : séance vide, sans devoir ce jour-là"}
    return {"hits": searcher.top(text[:4000], subject)}


def measure(conn: psycopg.Connection, output: dict, expected: dict,
            known: dict[int, tuple[str, str]] | None = None) -> dict[str, float | None]:
    known = known if known is not None else documents(conn)
    found = any(matches(wanted, hit, known) for wanted in expected.get("documents", []) for hit in output["hits"])
    return {"retrieval_recall": float(found)}
