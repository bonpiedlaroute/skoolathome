"""T25: the files teachers attach to a lesson in École Directe: downloaded once, indexed, cited."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from skoolathome.collectors.ecoledirecte import attachments
from skoolathome.collectors.ecoledirecte.client import EcoleDirecteClient, EcoleDirecteError, FileTooLarge
from skoolathome.evaluation import cases
from skoolathome.material import index
from skoolathome.rules import inputs
from skoolathome.schema import Document, Lesson, SchoolSnapshot
from skoolathome.store import db
from test_graph import GOOD, MATHS, FakeModels, statuses
from test_graph import run as run_evening
from test_material import FakeEmbedder, write_pdf

RUN = date(2026, 11, 13)


# ----------------------------------------------------------------------- the download


def server(answer):
    return EcoleDirecteClient("parent", "secret", transport=httpx.MockTransport(answer))


def test_a_file_is_downloaded_with_its_id_and_type():
    seen = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b"%PDF-1.7 the course", headers={"content-type": "application/pdf"})

    assert server(answer).download("9001", "FICHIER_CDT") == b"%PDF-1.7 the course"
    assert (seen["verbe"], seen["fichierId"], seen["leTypeDeFichier"]) == ("get", "9001", "FICHIER_CDT")
    assert seen["body"] == 'data={"forceDownload": 0}'


def test_an_error_in_json_and_a_file_too_large_are_refused():
    json_error = server(lambda r: httpx.Response(200, json={"code": 520, "message": "jeton invalide"},
                                                 headers={"content-type": "application/json"}))
    with pytest.raises(EcoleDirecteError, match="520"):
        json_error.download("1")
    big = server(lambda r: httpx.Response(200, content=b"%PDF" + b"x" * 2000, headers={"content-type": "application/pdf"}))
    with pytest.raises(FileTooLarge):
        big.download("1", max_bytes=1000)


# ------------------------------------------------------------------ during the collection


class FakeClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.calls = files, []

    def download(self, file_id: str, file_type: str = "FICHIER_CDT", *, max_bytes: int) -> bytes:
        self.calls.append(file_id)
        if file_id not in self.files:
            raise EcoleDirecteError(404, "fichier introuvable")
        return self.files[file_id]


def lesson(source_id: str, content: str, *documents: Document, day: date = RUN, code: str = "MATHS") -> Lesson:
    return Lesson(source="ecoledirecte", source_id=source_id, student_id="4242", day=day, subject_code=code,
                  subject=code, teacher="M. X", content=content, is_empty=not content and not documents,
                  documents=tuple(documents))


def snapshot(*lessons: Lesson) -> SchoolSnapshot:
    return SchoolSnapshot(student_id="4242", lessons=tuple(lessons))


def save(conn, *lessons_: Lesson) -> None:
    from skoolathome.store import school

    snap = snapshot(*lessons_)
    school.save_snapshot(conn, snap)
    attachments.register(conn, snap)


COURSE = Document(source_id="9001", name="cours-chapitre-4.pdf", kind="FICHIER_CDT", size=900)
SAME_AGAIN = Document(source_id="9002", name="cours-chapitre-4.pdf", kind="FICHIER_CDT", size=900)
PHOTO = Document(source_id="9003", name="corrige-exercices.png", kind="FICHIER_CDT", size=38000)
WORD = Document(source_id="9004", name="fiche.docx", kind="FICHIER_CDT", size=13000)
HUGE = Document(source_id="9005", name="exercices.pdf", kind="FICHIER_CDT", size=attachments.MAX_BYTES + 1)


def test_each_file_is_downloaded_once_whatever_the_collections(conn, tmp_path):
    pdf = write_pdf(tmp_path / "source.pdf", MATHS).read_bytes()
    save(conn, lesson("m1", "", COURSE, PHOTO, WORD, HUGE),
         lesson("m0", "Symétrie centrale.", SAME_AGAIN, day=date(2026, 11, 12)))
    client = FakeClient({"9001": pdf, "9002": pdf})
    root = tmp_path / "attachments"
    assert attachments.download_pending(conn, client, "4242", root=root, pause=0) == {"downloaded": 1, "failed": 0}
    # The same file attached to another lesson (same name, same size): not downloaded again.
    assert client.calls == ["9001"] and len(list(root.iterdir())) == 1
    assert attachments.counts(conn, "4242") == {"downloaded": 2, "unsupported": 2, "too_large": 1}
    assert attachments.unsupported_formats(conn, "4242") == {"png": 1, "docx": 1}
    save(conn, lesson("m1", "", COURSE, PHOTO, WORD, HUGE))  # collected again tomorrow
    attachments.download_pending(conn, client, "4242", root=root, pause=0)
    assert client.calls == ["9001"]


def test_a_failed_download_is_tried_again_three_times_at_most(conn, tmp_path):
    save(conn, lesson("m1", "", COURSE))
    client = FakeClient({})
    for _ in range(5):
        attachments.download_pending(conn, client, "4242", root=tmp_path, pause=0)
    assert client.calls == ["9001"] * attachments.MAX_ATTEMPTS
    assert attachments.counts(conn, "4242") == {"failed": 1}


def test_a_file_the_teacher_removed_is_forgotten_for_that_lesson(conn, tmp_path):
    save(conn, lesson("m1", "", COURSE, PHOTO))
    save(conn, lesson("m1", "", PHOTO))
    assert attachments.counts(conn, "4242") == {"unsupported": 1}


def test_the_collection_counts_what_is_not_processed(conn, tmp_path):
    from test_store import FakeClient as SchoolClient
    from skoolathome.collect import collect

    class WithFiles(SchoolClient):
        def download(self, file_id, file_type="FICHIER_CDT", *, max_bytes):
            return b"%PDF-1.4"

    client = WithFiles()
    for n, name in enumerate(["diary-2026-11-02", "diary-2026-11-03", "diary-2026-11-04"]):
        subject = client.responses[name]["data"]["matieres"][0]
        subject["contenuDeSeance"]["documents"] = [
            {"id": 70 + 2 * n, "libelle": f"cours-{n}.pdf", "type": "FICHIER_CDT", "taille": 1000},
            {"id": 71 + 2 * n, "libelle": f"photo-{n}.jpg", "type": "FICHIER_CDT", "taille": 1000}]
    result = collect(conn, client, today=date(2026, 11, 4), pause=0, attachments_root=tmp_path)
    files = result.counts["attachments"]
    assert files["downloaded"] >= 1 and files["new"]["downloaded"] == files["downloaded"]
    stored = conn.execute("SELECT counts FROM school.collection_runs WHERE id = %s", (result.run_id,)).fetchone()[0]
    assert stored["attachments"]["unsupported_formats"]["jpg"] == files["downloaded"]  # one photo per lesson


# ------------------------------------------------------------------ indexed, then cited


def downloaded(conn, tmp_path, *texts: str) -> None:
    pdf = write_pdf(tmp_path / "source.pdf", *texts).read_bytes()
    attachments.download_pending(conn, FakeClient({"9001": pdf}), "4242", root=tmp_path / "files", pause=0)


def test_an_attachment_is_indexed_even_without_the_embedding_model(conn, tmp_path):
    save(conn, lesson("m1", "", COURSE))
    downloaded(conn, tmp_path, MATHS, "Page deux : les axes de symétrie.")
    report = index.sync(conn, attachments.AttachmentSource(conn), None)
    assert len(report.indexed) == 1 and conn.execute("SELECT count(*) FROM material.chunks").fetchone()[0] == 0
    pages = attachments.lesson_pages(conn, "4242", [{"source": "ecoledirecte", "source_id": "m1"}])
    assert [(label, number) for _, label, number, _ in pages] == [("le document du prof, p. 1", 1),
                                                                   ("le document du prof, p. 2", 2)]
    # Once the embedding model is there, the same file gets its vectors.
    again = index.sync(conn, attachments.AttachmentSource(conn), FakeEmbedder())
    assert len(again.indexed) == 1 and conn.execute("SELECT count(*) FROM material.chunks").fetchone()[0] > 0
    assert index.sync(conn, attachments.AttachmentSource(conn), FakeEmbedder()).unchanged == 1
    # The plan counts the attachment's text with its lesson.
    [ref] = [l for l in inputs.load(conn, "4242", RUN).lessons if l.source_id == "m1"]
    assert ref.chars > 200


def test_a_lesson_with_only_its_pdf_gets_a_sheet_that_cites_it(conn, tmp_path):
    save(conn, lesson("m1", "", COURSE))
    downloaded(conn, tmp_path, MATHS)
    evening = run_evening(db.database_url(conn.info.dbname), FakeModels(maths=[GOOD]))
    assert statuses(evening) == [("2026-11-13-maths-exercises", "ready")]
    body = conn.execute("SELECT body FROM agent.sheets").fetchone()[0]
    [key] = body["sources"]
    assert key.startswith("doc:") and body["source_labels"][key] == "le document du prof, p. 1"
    assert all(item["quote"]["source_id"] == key for item in body["items"])
    model_input = conn.execute("SELECT model_input FROM agent.sheets").fetchone()[0]
    assert "Le prof a joint son document à la séance" in model_input["context"]
    # The lesson and its document: a free case of the « recherche » set.
    assert [c.input for c in cases.load(conn, "retrieval")] == []  # the lesson itself has no text to search with
    conn.execute("UPDATE school.lessons SET content = %s WHERE source_id = 'm1'", ("Symétrie centrale : " + MATHS,))
    assert cases.retrieval_cases(conn, "4242") == 1
    [case] = cases.load(conn, "retrieval")
    assert case.input == {"lesson": "m1"} and case.expected["documents"][0]["page"] is None
    assert json.dumps(case.input).find("Symétrie") == -1  # ids only, no text
