"""The PWA's home page (schema/home.py), computed from the local database, without any model.

Three blocks: what comes next (the timetable and the tests), the latest grades of each subject
with the class's average, lowest and highest, and the sheets to do before them. A suggestion rests
on the lessons École Directe gave: the teachers fill the notebook once the lesson is over, so the
latest lesson of a subject is what to revise before the next one.

Sent after each collection, by the evening, or by the next `skoolathome explain` pass when the API
could not take it (one signature): the page follows the data without a second login. The files
the teachers attached to the lessons shown go online with it (one signature each, only the ones
the API does not have yet), so that the child can open them from the PWA.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.rules import privacy
from skoolathome.rules.inputs import PAST_TEST_KINDS, upcoming_tests
from skoolathome.rules.plan import unique_tests
from skoolathome.schema.home import (
    Home,
    HomeAverages,
    HomeDocument,
    HomeGrade,
    HomeLesson,
    HomeLink,
    HomeRequest,
    HomeSlot,
    HomeSubjectGrades,
    HomeSuggestion,
    HomeTest,
)
from skoolathome.subjects import EXCLUDED_SUBJECTS, fr_day, label

HORIZON_DAYS = 14        # timetable and tests sent: the page stays right for a few days without collection
SUGGESTION_DAYS = 7      # suggestions for the lessons and tests of the coming week
GRADES_PER_SUBJECT = 3
REVISION_LOOKBACK_DAYS = 28
MAX_LESSONS = 3          # the lessons shown for a test; one for the next lesson
MAX_POINTS = 4
POINT_CHARS = 140
ONLINE_MAX_BYTES = 4_000_000   # one signed call per file: Vercel takes 4.5 MB per request
_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_BOILERPLATE = re.compile(r"^(feuille de route|comp[ée]tences?\b|travail [àa] faire|devoirs?\s*:)", re.IGNORECASE)
_LABEL = re.compile(r"^(objectifs?|th[èe]me|titre)\s*:\s*", re.IGNORECASE)
_BULLET = re.compile(r"^[\s\-–—•*·>]+|^\d+[.)]\s+")
_ANNOUNCED = re.compile(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi)(?:\s+(\d{1,2})\b)?", re.IGNORECASE)


# ----------------------------------------------------------------------------- helpers


def points(text: str, limit: int = MAX_POINTS) -> list[str]:
    """The first meaningful lines of a lesson, as the teacher wrote them: no bullets, no
    « Compétences : … », « Objectifs : » kept for what follows."""
    found = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        if len(line) < 3 or _BOILERPLATE.match(line):
            continue
        line = _LABEL.sub("", line)
        if len(line) > POINT_CHARS:
            line = line[:POINT_CHARS - 1].rsplit(" ", 1)[0] + "…"
        if line and line not in found:
            found.append(line)
        if len(found) == limit:
            break
    return found


def announced_day(text: str, due_on: date, known: set[date] = frozenset()) -> date | None:
    """The test day a homework names, after its due date: « l'évaluation … décalée au jeudi 19 » in a
    homework due on the 16th is on the 19th. A weekday without a number (« mercredi dst ») counts
    only when that day already has a test of the subject (`known`): the same test, seen twice."""
    for match in _ANNOUNCED.finditer(text):
        weekday = _DAYS.index(match.group(1).lower())
        number = int(match.group(2)) if match.group(2) else None
        for offset in range(1, 22 if number else 8):
            day = due_on + timedelta(days=offset)
            if day.weekday() == weekday and (day.day == number if number else day in known):
                return day
    return None


def _number(value) -> float | None:
    return float(value) if value is not None else None


def _hour(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


# ------------------------------------------------------------------------------ blocks


def _slots(conn: psycopg.Connection, student_id: str, today: date) -> tuple[list[HomeSlot], list[date]]:
    rows = conn.execute(
        "SELECT starts_at, ends_at, subject_code, subject, room, cancelled, kind FROM school.timetable_slots "
        "WHERE student_id = %s AND starts_at::date BETWEEN %s AND %s ORDER BY starts_at",
        (student_id, today, today + timedelta(days=HORIZON_DAYS)),
    ).fetchall()
    slots, holidays = [], set()
    for starts, ends, code, subject, room, cancelled, kind in rows:
        if kind == "CONGE":
            first, last = starts.date(), ends.date()
            holidays |= {first + timedelta(days=n) for n in range((last - first).days + 1)}
        elif kind == "COURS" and code:
            slots.append(HomeSlot(day=starts.date(), starts=_hour(starts), ends=_hour(ends), subject_code=code,
                                  subject=label(code, subject)[:60], room=room.strip()[:40], cancelled=cancelled))
    return slots, sorted(d for d in holidays if d >= today)


def _tests(conn: psycopg.Connection, student_id: str, today: date, names: list[str]) -> list[HomeTest]:
    """From today on: the tests of the homework (ticked, words, model), one per subject and day.
    A homework that names a later day for the test (« décalée au jeudi 19 ») counts on that day."""
    found = upcoming_tests(conn, student_id, today - timedelta(days=1), today + timedelta(days=HORIZON_DAYS))
    days: dict[str, set[date]] = {}
    for test in found:
        days.setdefault(test.subject_code, set()).add(test.day)
    moved = []
    for test in found:
        later = announced_day(test.text, test.day, days[test.subject_code]) if test.reason != "flag" else None
        moved.append(test if later is None else replace(test, day=later))
    tests = []
    for test in unique_tests(moved):
        what = " ".join(privacy.public_text(test.text, names).split())
        tests.append(HomeTest(day=test.day, subject_code=test.subject_code, subject=label(test.subject_code)[:60],
                              what=what[:239] + ("…" if len(what) > 239 else ""),
                              reason=test.reason if test.reason in ("flag", "keywords", "model") else "keywords"))
    return tests


def _on_20(value, out_of) -> float | None:
    return float(value) * 20 / float(out_of) if value is not None and out_of else None


def _mean(weighted: list[tuple[float, float]]) -> float | None:
    total = sum(weight for _, weight in weighted)
    return sum(value * weight for value, weight in weighted) / total if total else None


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _period_label(conn: psycopg.Connection, code: str) -> str:
    """« 1er trimestre »: the name École Directe gives the period, in its latest grades response."""
    row = conn.execute(
        "SELECT p->>'periode' FROM (SELECT body FROM raw.responses WHERE kind = 'grades' ORDER BY fetched_at DESC "
        "LIMIT 1) r CROSS JOIN LATERAL jsonb_array_elements(r.body->'data'->'periodes') AS p "
        "WHERE p->>'idPeriode' = %s LIMIT 1", (code,),
    ).fetchone()
    return (row[0] or "")[:40] if row else ""


def averages(conn: psycopg.Connection, student_id: str) -> tuple[HomeAverages | None, dict[str, tuple]]:
    """The averages of the current period (the one of the latest grade), as École Directe counts
    them: each grade on 20, weighted by its coefficient, without the grades marked « non
    significative »; the class's from the class average of each test. Then the general average:
    the subject averages added up and divided by their number, over the subjects that have both."""
    row = conn.execute("SELECT period FROM school.grades WHERE student_id = %s AND value IS NOT NULL "
                       "ORDER BY day DESC, source_id DESC LIMIT 1", (student_id,)).fetchone()
    if row is None:
        return None, {}
    rows = conn.execute(
        "SELECT subject_code, value, out_of, coefficient, class_average FROM school.grades "
        "WHERE student_id = %s AND period = %s AND significant AND value IS NOT NULL AND out_of > 0 "
        "AND coalesce(coefficient, 1) > 0", (student_id, row[0]),
    ).fetchall()
    child: dict[str, list[tuple[float, float]]] = {}
    klass: dict[str, list[tuple[float, float]]] = {}
    for code, value, out_of, coefficient, average in rows:
        weight = float(coefficient) if coefficient is not None else 1.0
        child.setdefault(code, []).append((_on_20(value, out_of), weight))
        if average is not None:
            klass.setdefault(code, []).append((_on_20(average, out_of), weight))
    subjects = {code: (_mean(child[code]), _mean(klass.get(code, []))) for code in child}
    both = [pair for pair in subjects.values() if pair[0] is not None and pair[1] is not None]
    general = HomeAverages(
        period=_period_label(conn, row[0]), subjects=len(both),
        average=_rounded(sum(a for a, _ in both) / len(both)) if both else None,
        class_average=_rounded(sum(c for _, c in both) / len(both)) if both else None,
    )
    return general, {code: (_rounded(mine), _rounded(theirs)) for code, (mine, theirs) in subjects.items()}


def _grades(conn: psycopg.Connection, student_id: str, names: list[str],
            subject_averages: dict[str, tuple] | None = None) -> list[HomeSubjectGrades]:
    """The latest grades of each subject, with its averages, the subjects graded most recently first."""
    rows = conn.execute(
        "SELECT subject_code, subject, day, kind, title, raw_value, value, out_of, coefficient, class_average, "
        "class_min, class_max FROM (SELECT g.*, row_number() OVER (PARTITION BY subject_code "
        "ORDER BY day DESC, source_id DESC) AS n FROM school.grades g WHERE student_id = %s AND raw_value <> '') g "
        "WHERE n <= %s ORDER BY subject_code, day DESC, n",
        (student_id, GRADES_PER_SUBJECT),
    ).fetchall()
    subjects: dict[str, HomeSubjectGrades] = {}
    for code, subject, day, kind, title, raw, value, out_of, coef, average, low, high in rows:
        mine, theirs = (subject_averages or {}).get(code, (None, None))
        entry = subjects.setdefault(code, HomeSubjectGrades(subject_code=code, subject=label(code, subject)[:60],
                                                            grades=[], average=mine, class_average=theirs))
        entry.grades.append(HomeGrade(
            day=day, kind=kind[:40], title=privacy.public_text(title, names)[:120], value=raw.strip()[:12],
            score=_number(value), out_of=_number(out_of), coefficient=_number(coef),
            class_average=_number(average), class_min=_number(low), class_max=_number(high)))
    return sorted(subjects.values(), key=lambda s: (-s.grades[0].day.toordinal(), s.subject))


def _documents(conn: psycopg.Connection, student_id: str, lesson_ids: list[str],
               names: list[str]) -> list[HomeDocument]:
    """The files attached to these lessons. With its sha256 when the PWA can open it: downloaded
    during a collection, still on disk, small enough for one call."""
    rows = conn.execute(
        "SELECT d->>'name', (d->>'size')::bigint, a.sha256, a.path, a.status FROM school.lessons l "
        "CROSS JOIN LATERAL jsonb_array_elements(l.documents) AS d LEFT JOIN school.attachments a "
        "ON a.source = l.source AND a.student_id = l.student_id AND a.lesson_source_id = l.source_id "
        "AND a.document_id = d->>'source_id' WHERE l.student_id = %s AND l.source_id = ANY(%s) "
        "ORDER BY l.source_id, d->>'source_id'", (student_id, lesson_ids),
    ).fetchall()
    found, seen = [], set()
    for name, size, sha, path, status in rows:
        online = (status == "downloaded" and sha and path and Path(path).is_file()
                  and Path(path).stat().st_size <= ONLINE_MAX_BYTES)
        key = sha if online else name
        if key in seen:
            continue
        seen.add(key)
        found.append(HomeDocument(name=privacy.public_text(name or "document", names)[:160], size=size,
                                  sha256=sha if online else None))
    return found[:12]


def _lessons_before(conn: psycopg.Connection, student_id: str, code: str, before: date, since: date,
                    count: int, names: list[str]) -> list[HomeLesson]:
    """The latest lessons of the subject with something written (or a document attached), before a
    day, with the files and the web pages the teacher gave."""
    rows = conn.execute(
        "SELECT l.day, string_agg(CASE WHEN l.is_empty THEN '' ELSE l.content END, E'\n' ORDER BY l.source_id), "
        "bool_or(jsonb_array_length(l.documents) > 0), array_agg(l.source_id ORDER BY l.source_id), "
        "jsonb_agg(l.links ORDER BY l.source_id) FROM school.lessons l "
        "WHERE l.student_id = %s AND l.subject_code = %s AND l.day < %s AND l.day > %s "
        "GROUP BY l.day HAVING bool_or(NOT l.is_empty) OR bool_or(jsonb_array_length(l.documents) > 0) "
        "ORDER BY l.day DESC LIMIT %s",
        (student_id, code, before, since, count),
    ).fetchall()
    lessons = []
    for day, text, attached, lesson_ids, link_lists in rows:
        links: dict[str, HomeLink] = {}
        for item in (link for found in link_lists for link in found):
            if item.get("url", "").startswith(("http://", "https://")) and item["url"] not in links:
                links[item["url"]] = HomeLink(url=item["url"][:500], label=item.get("label", "")[:120])
        lessons.append(HomeLesson(
            day=day, points=points(privacy.public_text(text or "", names)), attachment=bool(attached),
            documents=_documents(conn, student_id, lesson_ids, names) if attached else [],
            links=list(links.values())[:10]))
    return lessons


def _sheets_for(conn: psycopg.Connection, student_id: str, code: str, lessons: list[HomeLesson],
                test_day: date | None) -> list[str]:
    """Sheets made for it: a revision for that test, or a sheet citing one of these lessons."""
    keys = [f"lesson:{lesson.day.isoformat()}:{code}" for lesson in lessons]
    rows = conn.execute(
        "SELECT id FROM agent.sheets WHERE student_id = %s AND subject_code = %s AND status IN ('ready', 'published') "
        "AND body IS NOT NULL AND ((%s::date IS NOT NULL AND kind = 'test_revision' AND due_by = %s) "
        "OR (body->'sources') ?| %s) ORDER BY visible_from DESC, id LIMIT 5",
        (student_id, code, test_day, test_day, keys),
    ).fetchall()
    return [row[0] for row in rows]


def _previous_test(conn: psycopg.Connection, student_id: str, code: str, before: date) -> date | None:
    row = conn.execute(
        "SELECT max(day) FROM school.grades WHERE student_id = %s AND subject_code = %s AND day < %s "
        "AND lower(kind) ~ %s", (student_id, code, before, PAST_TEST_KINDS),
    ).fetchone()
    return row[0] if row else None


def _suggestions(conn: psycopg.Connection, student_id: str, today: date, slots: list[HomeSlot],
                 tests: list[HomeTest], names: list[str]) -> list[HomeSuggestion]:
    until = today + timedelta(days=SUGGESTION_DAYS)
    found: list[HomeSuggestion] = []
    tested = set()
    for test in tests:
        if test.day > until or test.subject_code in EXCLUDED_SUBJECTS:
            continue
        since = max(d for d in (_previous_test(conn, student_id, test.subject_code, test.day),
                                test.day - timedelta(days=REVISION_LOOKBACK_DAYS)) if d is not None)
        lessons = _lessons_before(conn, student_id, test.subject_code, min(test.day, today + timedelta(days=1)),
                                  since, MAX_LESSONS, names)
        if not lessons:
            continue
        tested.add((test.subject_code, test.day))
        focus = f"le contrôle du {fr_day(test.day)}" + (f" : {test.what}" if test.what else "")
        found.append(HomeSuggestion(
            subject_code=test.subject_code, subject=test.subject, reason="test", for_day=test.day, lessons=lessons,
            sheet_ids=_sheets_for(conn, student_id, test.subject_code, lessons, test.day),
            request=HomeRequest(kind="revision", focus=focus[:300])))
    upcoming = sorted({(s.day, s.subject_code, s.subject) for s in slots
                       if today < s.day <= until and not s.cancelled and s.subject_code not in EXCLUDED_SUBJECTS})
    for day, code, subject in upcoming:
        if (code, day) in tested:
            continue
        lessons = _lessons_before(conn, student_id, code, min(day, today + timedelta(days=1)),
                                  day - timedelta(days=REVISION_LOOKBACK_DAYS), 1, names)
        if not lessons or not (lessons[0].points or lessons[0].attachment):
            continue
        lesson = lessons[0]
        focus = f"la séance du {fr_day(lesson.day)}" + (f" : {lesson.points[0]}" if lesson.points else "")
        found.append(HomeSuggestion(
            subject_code=code, subject=subject, reason="lesson", for_day=day, lessons=lessons,
            sheet_ids=_sheets_for(conn, student_id, code, lessons, None),
            request=HomeRequest(kind="extra", focus=focus[:300])))
    return found[:40]


def compute(conn: psycopg.Connection, student_id: str, today: date) -> Home:
    names = privacy.teachers(conn)
    slots, holidays = _slots(conn, student_id, today)
    tests = _tests(conn, student_id, today, names)
    collected = conn.execute(
        "SELECT max(finished_at) FROM school.collection_runs WHERE status IN ('ok', 'partial') AND student_id = %s",
        (student_id,),
    ).fetchone()[0]
    general, subject_averages = averages(conn, student_id)
    return Home(generated_at=datetime.now().astimezone(), collected_at=collected, slots=slots[:200],
                holidays=holidays[:60], tests=tests[:40],
                grades=_grades(conn, student_id, names, subject_averages)[:30], averages=general,
                suggestions=_suggestions(conn, student_id, today, slots, tests, names))


# ------------------------------------------------------------------------------ sending


def latest_run(conn: psycopg.Connection) -> int | None:
    row = conn.execute("SELECT max(id) FROM school.collection_runs WHERE status IN ('ok', 'partial')").fetchone()
    return row[0] if row else None


def pending(conn: psycopg.Connection) -> bool:
    """A collection, or a sheet published, since the page last went online."""
    row = conn.execute("SELECT max(run_id), max(sent_at) FROM agent.home_sends").fetchone()
    run = latest_run(conn)
    if run is None:
        return False
    if row[0] is None or run > row[0]:
        return True
    published = conn.execute("SELECT max(published_at) FROM agent.sheets").fetchone()[0]
    return published is not None and published > row[1]


def _files(page: Home) -> set[str]:
    return {d.sha256 for s in page.suggestions for lesson in s.lessons for d in lesson.documents if d.sha256}


def _without(page: Home, missing: set[str]) -> Home:
    """The page without the files that could not go online: shown as « sur École Directe »."""
    def lesson(item: HomeLesson) -> HomeLesson:
        return item.model_copy(update={"documents": [
            d.model_copy(update={"sha256": None}) if d.sha256 in missing else d for d in item.documents]})
    return page.model_copy(update={"suggestions": [
        s.model_copy(update={"lessons": [lesson(item) for item in s.lessons]}) for s in page.suggestions]})


def plain_name(name: str) -> str:
    """The file's name as it travels in the signed URL: letters, digits, « . », « - », « _ ». Vercel
    hands the API a query string re-encoded when it has accents, spaces or brackets, and the
    signature no longer matches. Only the name of the downloaded file; the PWA shows the real one."""
    import unicodedata

    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    stem, dot, suffix = ascii_.rpartition(".")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem if dot else ascii_).strip("-") or "document"
    suffix = re.sub(r"[^A-Za-z0-9]+", "", suffix) if dot else ""
    return (stem[:150] + (f".{suffix[:8]}" if suffix else ""))


def upload_files(conn: psycopg.Connection, client, page: Home) -> Home:
    """Each file of the page the API does not have yet: one signed call per file. The API keeps
    only the files of the latest page."""
    wanted = _files(page)
    if not wanted:
        return page
    failed = set()
    rows = conn.execute("SELECT DISTINCT ON (sha256) sha256, path, name FROM school.attachments "
                        "WHERE sha256 = ANY(%s) AND status = 'downloaded'", (sorted(wanted),)).fetchall()
    on_disk = {sha: (path, name) for sha, path, name in rows}
    for sha in client.missing_documents(sorted(wanted)):
        path, name = on_disk.get(sha, (None, None))
        try:
            content = Path(path).read_bytes() if path else None
        except OSError:
            content = None
        if content is None or len(content) > ONLINE_MAX_BYTES:
            failed.add(sha)
            continue
        kind = mimetypes.guess_type(name or path)[0] or "application/octet-stream"
        client.upload_document(sha, plain_name(name or Path(path).name), kind, content)
    return _without(page, failed) if failed else page


def send(conn: psycopg.Connection, client, student_id: str, today: date) -> Home:
    """Computed now, its files put online, then the page with one signature, remembered."""
    page = upload_files(conn, client, compute(conn, student_id, today))
    client.home(page)
    conn.execute("INSERT INTO agent.home_sends (run_id, body) VALUES (%s, %s)",
                 (latest_run(conn), Jsonb(page.model_dump(mode="json"))))
    return page


# ------------------------------------------------------------------------- as text


def next_school_day(page: Home, today: date) -> date | None:
    """The first day after today with a lesson that is not cancelled: tomorrow, or Monday on a Friday."""
    days = sorted({s.day for s in page.slots if s.day > today and not s.cancelled and s.day not in page.holidays})
    return days[0] if days else None


def describe(page: Home, today: date) -> str:
    """The three blocks as the PWA shows them, for the terminal."""
    lines = []
    day = next_school_day(page, today)
    lines.append(f"À venir, {fr_day(day)} :" if day else "À venir : pas de cours connu dans les deux semaines.")
    for slot in (s for s in page.slots if s.day == day):
        lines.append(f"  {slot.starts}  {slot.subject}" + ("  (annulé)" if slot.cancelled else ""))
    soon = [t for t in page.tests if today <= t.day <= today + timedelta(days=3)]
    lines.append("Contrôles jusqu'à J+3 :" + ("" if soon else " aucun"))
    for test in soon:
        lines.append(f"  {fr_day(test.day)}  {test.subject} — {test.what[:90]}")
    general = page.averages
    if general and general.average is not None:
        lines.append(f"\nMoyenne générale ({general.period or 'période en cours'}, {general.subjects} matières) : "
                     f"{general.average:g}, classe {general.class_average:g}")
    lines.append("\nDernières notes :")
    for subject in page.grades:
        grade = subject.grades[0]
        out_of = f"/{grade.out_of:g}" if grade.out_of else ""
        klass = (f"  classe {grade.class_average:g} (min {grade.class_min:g}, max {grade.class_max:g})"
                 if grade.class_average is not None and grade.class_min is not None and grade.class_max is not None else "")
        mean = (f"  moyenne {subject.average:g}" + (f" / classe {subject.class_average:g}"
                                                    if subject.class_average is not None else "")
                if subject.average is not None else "")
        lines.append(f"  {subject.subject:<22} {grade.value}{out_of}  {fr_day(grade.day)}  {grade.kind}{klass}{mean}")
    shown = [s for s in page.suggestions if (s.reason == "lesson" and s.for_day == day)
             or (s.reason == "test" and today <= s.for_day <= today + timedelta(days=3))]
    lines.append("\nFiches suggérées :" + ("" if shown else " aucune"))
    for suggestion in shown:
        why = f"contrôle {fr_day(suggestion.for_day)}" if suggestion.reason == "test" else f"cours {fr_day(suggestion.for_day)}"
        lines.append(f"  {suggestion.subject} ({why})" + (f" : fiche {', '.join(suggestion.sheet_ids)}"
                                                         if suggestion.sheet_ids else " : à demander"))
        for lesson in suggestion.lessons:
            files = (f" [{len(lesson.documents)} document(s), {sum(1 for d in lesson.documents if d.sha256)} en ligne]"
                     if lesson.documents else " [document du prof]" if lesson.attachment else "")
            lines.append(f"    {fr_day(lesson.day)} : " + " / ".join(lesson.points)[:150] + files
                         + (f" [liens : {', '.join(link.label for link in lesson.links)}]" if lesson.links else ""))
    return "\n".join(lines)
