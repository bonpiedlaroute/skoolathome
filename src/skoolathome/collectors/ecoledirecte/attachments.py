"""Files the teachers attach to a lesson in École Directe (T25, ARCHITECTURE.md §5.3 and §5.4).

The best possible source for a sheet: École Directe itself links it to the lesson, no search
needed. During the collection, in the same session as the rest (one login a day):

1. `register` lists the attachments of every lesson collected, with their status: a file is
   `pending`, whatever its format; a file above the limit is `too_large`.
2. `download_pending` downloads each pending file once, with a pause between two files and a
   maximum per run. A file is kept as data/attachments/<sha256>.<suffix> (data/ is never
   committed): the same file attached twice, or found again, is neither downloaded nor indexed
   again. A file that is not what its name says (an error page instead of a PDF) is `unsupported`.
3. `AttachmentSource` hands the PDFs to the index of T18: pages, OCR when needed, vectors. The
   other formats (images, Word, GeoGebra…) are for the PWA only: the child opens them from the
   home page, to go further (home.py).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
import psycopg

from skoolathome.collectors.ecoledirecte import mapping
from skoolathome.collectors.ecoledirecte.client import EcoleDirecteError, FileTooLarge, TwoFactorRequired
from skoolathome.schema import SchoolSnapshot
from skoolathome.schema.material import CourseDocument, DocumentRef, Page

ROOT = Path("data/attachments")
INDEXED = {".pdf"}         # the other formats are only offered on the PWA
# What a file must start with, when its name says what it is: École Directe answers an error with
# a page of HTML or JSON, never kept as a course document.
MAGIC = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8",), ".jpeg": (b"\xff\xd8",),
    ".gif": (b"GIF8",),
    ".docx": (b"PK",), ".pptx": (b"PK",), ".xlsx": (b"PK",), ".odt": (b"PK",), ".odp": (b"PK",),
    ".ods": (b"PK",), ".ggb": (b"PK",), ".zip": (b"PK",),
    ".doc": (b"\xd0\xcf\x11\xe0",), ".ppt": (b"\xd0\xcf\x11\xe0",), ".xls": (b"\xd0\xcf\x11\xe0",),
}
MAX_BYTES = 20 * 2**20     # 20 Mo: a lesson's PDF is well below (the largest seen: 3.4 Mo)
MAX_PER_RUN = 20           # the first collection finds about thirty: two evenings
MAX_ATTEMPTS = 3
CODE_FORBIDDEN = 403
LABEL = "le document du prof, p. {page}"  # how a sheet cites it: « D'après … le document du prof, p. 2 »


def _status(name: str, size: int | None) -> str:
    return "too_large" if (size or 0) > MAX_BYTES else "pending"


def _suffix(name: str) -> str:
    """The file's suffix as kept on disk: its own, if it is a plain one."""
    suffix = Path(name).suffix.lower()
    return suffix if suffix[1:].isalnum() and len(suffix) <= 6 else ""


def _expected(name: str, content: bytes) -> bool:
    """The file is what its name says. An unknown format only must not be a page of HTML or JSON."""
    starts = MAGIC.get(_suffix(name))
    if starts is not None:
        return content.startswith(starts)
    return bool(content) and not content.lstrip()[:1] in (b"<", b"{")


def register(conn: psycopg.Connection, snapshot: SchoolSnapshot) -> None:
    """The attachments of the lessons just collected. A file the teacher removed from a lesson is
    forgotten for that lesson; a known one keeps its status."""
    for lesson in snapshot.lessons:
        ids = [d.source_id for d in lesson.documents]
        conn.execute("DELETE FROM school.attachments WHERE source = %s AND student_id = %s AND lesson_source_id = %s "
                     "AND NOT document_id = ANY(%s)", (lesson.source, snapshot.student_id, lesson.source_id, ids))
        for document in lesson.documents:
            conn.execute(
                "INSERT INTO school.attachments (source, student_id, lesson_source_id, document_id, name, kind, size, "
                "status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (lesson.source, snapshot.student_id, lesson.source_id, document.source_id, document.name,
                 document.kind, document.size, _status(document.name, document.size)),
            )


def _set(conn: psycopg.Connection, student_id: str, document_id: str, status: str, *, error: str = "",
         sha256: str | None = None, path: str | None = None, attempt: bool = False) -> None:
    conn.execute(
        "UPDATE school.attachments SET status = %s, error = %s, sha256 = coalesce(%s, sha256), "
        "path = coalesce(%s, path), attempts = attempts + %s, "
        "downloaded_at = CASE WHEN %s = 'downloaded' THEN now() ELSE downloaded_at END "
        "WHERE source = %s AND student_id = %s AND document_id = %s",
        (status, error[:300], sha256, path, int(attempt), status, mapping.SOURCE, student_id, document_id),
    )


def download_pending(conn: psycopg.Connection, client, student_id: str, *, root: Path = ROOT,
                     pause: float = 1.0, limit: int = MAX_PER_RUN) -> dict[str, int]:
    """Each pending file once. A refusal from École Directe (403) stops the downloads, never the
    collection; a failed file is tried again at the next collections, three times at most."""
    # The latest lessons first: their files are the ones the home page offers.
    rows = conn.execute(
        "SELECT a.document_id, min(a.kind), min(a.name) FROM school.attachments a LEFT JOIN school.lessons l "
        "ON l.source = a.source AND l.student_id = a.student_id AND l.source_id = a.lesson_source_id "
        "WHERE a.source = %s AND a.student_id = %s "
        "AND (a.status = 'pending' OR (a.status = 'failed' AND a.attempts < %s)) GROUP BY a.document_id "
        "ORDER BY max(l.day) DESC NULLS LAST, min(a.first_seen), a.document_id LIMIT %s",
        (mapping.SOURCE, student_id, MAX_ATTEMPTS, limit),
    ).fetchall()
    done = {"downloaded": 0, "failed": 0}
    for document_id, kind, name in rows:
        # Attached to another lesson too, or the same file attached again (same name, same size):
        # already here, no call to École Directe.
        known = conn.execute(
            "SELECT k.sha256, k.path FROM school.attachments k, school.attachments a WHERE k.source = %(s)s "
            "AND k.student_id = %(st)s AND k.status = 'downloaded' AND a.source = %(s)s AND a.student_id = %(st)s "
            "AND a.document_id = %(d)s AND (k.document_id = a.document_id OR (k.name = a.name AND k.size = a.size)) "
            "LIMIT 1", {"s": mapping.SOURCE, "st": student_id, "d": document_id},
        ).fetchone()
        if known:
            _set(conn, student_id, document_id, "downloaded", sha256=known[0], path=known[1])
            continue
        try:
            content = client.download(document_id, kind, max_bytes=MAX_BYTES)
        except FileTooLarge as error:
            _set(conn, student_id, document_id, "too_large", error=str(error))
            continue
        except TwoFactorRequired:
            break
        except EcoleDirecteError as error:
            _set(conn, student_id, document_id, "failed", error=str(error), attempt=True)
            done["failed"] += 1
            if error.code == CODE_FORBIDDEN:
                break  # never insist after a refusal
            continue
        except httpx.HTTPError as error:
            _set(conn, student_id, document_id, "failed", error=f"{type(error).__name__}: {error}", attempt=True)
            done["failed"] += 1
            continue
        finally:
            if pause:
                time.sleep(pause)
        if not _expected(name, content):
            _set(conn, student_id, document_id, "unsupported",
                 error=f"le fichier reçu n'est pas un {_suffix(name)[1:].upper() or 'fichier'}")
            continue
        sha = hashlib.sha256(content).hexdigest()
        path = root / f"{sha}{_suffix(name)}"
        if not path.exists():
            root.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        _set(conn, student_id, document_id, "downloaded", sha256=sha, path=str(path))
        done["downloaded"] += 1
    return done


def counts(conn: psycopg.Connection, student_id: str) -> dict[str, int]:
    """Files per status, for collection_runs: what is not processed shows there."""
    rows = conn.execute(
        "SELECT status, count(DISTINCT document_id) FROM school.attachments WHERE source = %s AND student_id = %s "
        "GROUP BY status", (mapping.SOURCE, student_id),
    ).fetchall()
    return dict(rows)


def unsupported_formats(conn: psycopg.Connection, student_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT lower(substring(name from '\\.([^.]+)$')), count(DISTINCT document_id) FROM school.attachments "
        "WHERE source = %s AND student_id = %s AND status = 'unsupported' GROUP BY 1", (mapping.SOURCE, student_id),
    ).fetchall()
    return {suffix or "sans extension": count for suffix, count in rows}


class AttachmentSource:
    """The downloaded attachments, as course material for the index of T18: one document per file
    (its sha256), however many lessons it is attached to. It takes the subject of its lesson; its
    pages are numbered from 1."""

    name = mapping.SOURCE

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.paths: dict[str, str] = {}

    def list_documents(self) -> list[DocumentRef]:
        rows = self.conn.execute(
            "SELECT DISTINCT ON (a.sha256) a.sha256, a.name, a.path, l.subject_code "
            "FROM school.attachments a JOIN school.lessons l ON l.source = a.source "
            "AND l.student_id = a.student_id AND l.source_id = a.lesson_source_id "
            "WHERE a.status = 'downloaded' AND a.path IS NOT NULL AND lower(a.path) LIKE '%%.pdf' "
            "ORDER BY a.sha256, a.first_seen",
        ).fetchall()
        refs = []
        for sha, name, path, subject in rows:
            if not Path(path).is_file():
                continue  # data/ wiped: downloaded again only if the status is reset
            self.paths[sha] = path
            refs.append(DocumentRef(source=self.name, source_ref=sha, title=Path(name).stem, sha256=sha,
                                    subject_code=subject))
        return refs

    def fetch(self, ref: DocumentRef) -> CourseDocument:
        from skoolathome.material.extract import extract  # PyMuPDF: only when indexing

        extracted = extract(Path(self.paths[ref.source_ref]))
        return CourseDocument(ref=ref, pages=tuple(Page(number=index + 1, text=text, ocr=extracted.ocr)
                                                   for index, text in enumerate(extracted.pages)))


def lesson_pages(conn: psycopg.Connection, student_id: str, lessons: list[dict]) -> list[tuple[str, str, int, str]]:
    """The pages of the files attached to these lessons, most recent lesson first: (key, label,
    page, text). The key is the one a search hit would have: doc:<id>:p<n>."""
    ids = [ref["source_id"] for ref in lessons]
    if not ids:
        return []
    rows = conn.execute(
        "SELECT d.id, p.number, p.text FROM school.attachments a "
        "JOIN school.lessons l ON l.source = a.source AND l.student_id = a.student_id AND l.source_id = a.lesson_source_id "
        "JOIN material.documents d ON d.source = a.source AND d.source_ref = a.sha256 AND d.status = 'indexed' "
        "JOIN material.pages p ON p.document_id = d.id "
        "WHERE a.student_id = %s AND a.lesson_source_id = ANY(%s) AND a.status = 'downloaded' "
        "AND length(btrim(p.text)) > 0 ORDER BY l.day DESC, d.id, p.number",
        (student_id, ids),
    ).fetchall()
    seen, pages = set(), []
    for doc_id, number, text in rows:
        key = f"doc:{doc_id}:p{number}"
        if key not in seen:
            seen.add(key)
            pages.append((key, LABEL.format(page=number), number, text))
    return pages
