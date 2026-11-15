"""Migrations, collection without duplicates, run log. Needs the local Postgres."""

from __future__ import annotations

import base64
import copy
from datetime import date

from conftest import load
from skoolathome import summary
from skoolathome.collect import collect, pick_student
from skoolathome.collectors.ecoledirecte.client import EcoleDirecteError, TwoFactorRequired
from skoolathome.store.migrate import migrate

TODAY = date(2026, 11, 4)
ALL_MODULES = ["CAHIER_DE_TEXTES", "EDT", "NOTES"]


class FakeClient:
    """Answers like EcoleDirecteClient, from the anonymized fixtures."""

    version = "test"

    def __init__(self, modules: list[str] = ALL_MODULES) -> None:
        self.modules = modules
        self.calls: list[str] = []
        self.connects = 0
        self.responses = {
            name: load(name)
            for name in ("timetable", "homework", "grades", "diary-2026-11-02", "diary-2026-11-03", "diary-2026-11-04")
        }
        self.fail: dict[str, Exception] = {}

    def connect(self, answer=None):
        self.connects += 1
        if "login" in self.fail:
            raise self.fail["login"]

    def students(self):
        return [{"id": 42, "first_name": "Léo", "class_name": "5E B", "modules": self.modules}]

    def _answer(self, name: str, empty: dict):
        self.calls.append(name)
        if name.split("-")[0] in self.fail:
            raise self.fail[name.split("-")[0]]
        return copy.deepcopy(self.responses.get(name, empty))

    def timetable(self, student_id, start, end):
        return self._answer("timetable", {"data": []})

    def upcoming_homework(self, student_id):
        return self._answer("homework", {"data": {}})

    def grades(self, student_id):
        return self._answer("grades", {"data": {"notes": []}})

    def day_diary(self, student_id, day):
        return self._answer(f"diary-{day.isoformat()}", {"data": {"date": day.isoformat(), "matieres": []}})


def run(conn, client):
    return collect(conn, client, today=TODAY, pause=0)


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM school.{table}").fetchone()[0]


def test_migrations_apply_once_and_vector_is_installed(conn):
    assert migrate(conn) == []  # the fixture already applied them
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == ["0001", "0002", "0003", "0004", "0005", "0006", "0007"]
    assert conn.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()[0] == 1.0


def test_first_collection_saves_everything(conn):
    result = run(conn, FakeClient())
    assert result.status == "ok" and result.student_id == "42"
    assert count(conn, "timetable_slots") > 0 and count(conn, "lessons") > 0
    assert count(conn, "homework") > 0 and count(conn, "grades") > 0
    assert conn.execute("SELECT count(*) FROM raw.responses WHERE run_id = %s", (result.run_id,)).fetchone()[0] >= 4
    # the session token is never stored
    assert conn.execute("SELECT count(*) FROM raw.responses WHERE body ? 'token'").fetchone()[0] == 0


def test_two_collections_create_no_duplicate(conn):
    run(conn, FakeClient())
    before = {t: count(conn, t) for t in ("timetable_slots", "lessons", "homework", "grades")}
    second = run(conn, FakeClient())
    assert {t: count(conn, t) for t in before} == before
    for table, counts in second.counts.items():
        assert set(counts) <= {"unchanged"}, table


def test_changed_lesson_is_updated(conn):
    client = FakeClient()
    run(conn, client)
    subject = next(m for m in client.responses["diary-2026-11-02"]["data"]["matieres"] if m.get("contenuDeSeance"))
    key = (str(subject["id"]),)
    before = conn.execute("SELECT fingerprint, content FROM school.lessons WHERE source_id = %s", key).fetchone()
    subject["contenuDeSeance"]["contenu"] = base64.b64encode("<p>Contenu modifié</p>".encode()).decode()
    second = run(conn, client)
    after = conn.execute("SELECT fingerprint, content FROM school.lessons WHERE source_id = %s", key).fetchone()
    assert after[1] == "Contenu modifié" and after[0] != before[0]
    assert second.counts["lessons"]["updated"] == 1


def test_validation_error_is_logged_and_the_rest_is_saved(conn):
    client = FakeClient()
    del client.responses["grades"]["data"]["notes"][0]["typeDevoir"]
    result = run(conn, client)
    assert result.status == "partial"
    assert count(conn, "grades") == 0 and count(conn, "lessons") > 0
    status, errors = conn.execute("SELECT status, errors FROM school.collection_runs WHERE id = %s", (result.run_id,)).fetchone()
    assert status == "partial"
    assert errors[0]["kind"] == "grades" and "typeDevoir" in errors[0]["error"]


def test_two_factor_request_stops_the_run_without_retry(conn):
    client = FakeClient()
    client.fail["login"] = TwoFactorRequired(250, "double authentification requise")
    result = run(conn, client)
    assert result.status == "two_factor_required"
    assert client.connects == 1 and client.calls == []
    assert conn.execute("SELECT status FROM school.collection_runs WHERE id = %s", (result.run_id,)).fetchone()[0] == "two_factor_required"


def test_refused_call_stops_everything(conn):
    client = FakeClient()
    client.fail["homework"] = EcoleDirecteError(403, "accès refusé")
    result = run(conn, client)
    assert result.status == "failed"
    assert client.calls == ["timetable", "homework"]  # nothing is called after the refusal


def test_disabled_module_is_never_called(conn):
    client = FakeClient(modules=["CAHIER_DE_TEXTES", "EDT"])
    result = run(conn, client)
    assert "grades" not in client.calls
    assert result.status == "partial" and any(e["kind"] == "grades" for e in result.errors)


def test_pick_student_takes_the_only_one_with_a_homework_diary():
    students = [{"id": 1, "modules": []}, {"id": 2, "modules": ["CAHIER_DE_TEXTES"]}, {"id": 3, "modules": []}]
    assert pick_student(students, None)["id"] == 2
    assert pick_student(students, 3)["id"] == 3


def test_summary_lists_flagged_homework(conn):
    client = FakeClient()
    for items in client.responses["homework"]["data"].values():
        for item in items:
            item["interrogation"] = True
    run(conn, client)
    text = summary.build(conn, "42", TODAY)
    assert "## Contrôles des 14 prochains jours" in text
    assert "case « contrôle » cochée par le prof" in text
    assert "Statut : `ok`" in text
