-- T25: the files teachers attach to a lesson in École Directe. Downloaded once, kept in
-- data/attachments/ (never committed), indexed like the drop folder (T18), linked to their lesson.

CREATE TABLE school.attachments (
    source           text NOT NULL,
    student_id       text NOT NULL,
    lesson_source_id text NOT NULL,   -- school.lessons.source_id: the lesson it is attached to
    document_id      text NOT NULL,   -- École Directe's id of the file
    name             text NOT NULL,
    kind             text NOT NULL,   -- École Directe's file type, needed to download it (FICHIER_CDT)
    size             bigint,
    status           text NOT NULL CHECK (status IN ('pending', 'downloaded', 'unsupported', 'too_large', 'failed')),
    error            text NOT NULL DEFAULT '',
    attempts         integer NOT NULL DEFAULT 0,
    sha256           text,            -- an unchanged file is never downloaded nor indexed again
    path             text,            -- data/attachments/<sha256>.pdf
    first_seen       timestamptz NOT NULL DEFAULT now(),
    downloaded_at    timestamptz,
    PRIMARY KEY (source, student_id, lesson_source_id, document_id)
);
CREATE INDEX attachments_document_idx ON school.attachments (document_id);

-- The reference set gets new families: retrieval (T25, T31), instructions (T28, T31),
-- explanations (T29).
ALTER TABLE agent.eval_cases DROP CONSTRAINT eval_cases_family_check;
ALTER TABLE agent.eval_cases ADD CONSTRAINT eval_cases_family_check
    CHECK (family IN ('sheets', 'tests', 'retrieval', 'instructions', 'explanations'));
ALTER TABLE agent.eval_cases DROP CONSTRAINT eval_cases_origin_check;
ALTER TABLE agent.eval_cases ADD CONSTRAINT eval_cases_origin_check
    CHECK (origin IN ('real', 'trap', 'report', 'attachment', 'proposal', 'explanation'));
