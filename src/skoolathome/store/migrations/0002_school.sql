-- One row per run of the collector: what it did, and what went wrong.
CREATE TABLE school.collection_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text NOT NULL,
    student_id  text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running',  -- running | ok | partial | two_factor_required | failed
    counts      jsonb NOT NULL DEFAULT '{}',      -- per table: inserted, updated, unchanged
    errors      jsonb NOT NULL DEFAULT '[]'       -- validation and call errors
);

-- Raw responses, kept so the mapping can be replayed after a fix.
CREATE TABLE raw.responses (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      bigint NOT NULL REFERENCES school.collection_runs (id) ON DELETE CASCADE,
    source      text NOT NULL,
    kind        text NOT NULL,   -- timetable | homework | diary | grades
    ref         text NOT NULL DEFAULT '',  -- the day, for a diary response
    api_version text NOT NULL DEFAULT '',
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    body        jsonb NOT NULL
);
CREATE INDEX responses_run_idx ON raw.responses (run_id);

CREATE TABLE school.timetable_slots (
    source       text NOT NULL,
    student_id   text NOT NULL,
    source_id    text NOT NULL,
    starts_at    timestamp NOT NULL,  -- local school time, as given by the source
    ends_at      timestamp NOT NULL,
    subject_code text NOT NULL,
    subject      text NOT NULL,
    teacher      text NOT NULL,
    room         text NOT NULL,
    kind         text NOT NULL,
    cancelled    boolean NOT NULL,
    fingerprint  text NOT NULL,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    -- starts_at is in the key: the source gives id 0 to every holiday.
    PRIMARY KEY (source, student_id, source_id, starts_at)
);
CREATE INDEX timetable_slots_time_idx ON school.timetable_slots (student_id, starts_at);

CREATE TABLE school.lessons (
    source       text NOT NULL,
    student_id   text NOT NULL,
    source_id    text NOT NULL,
    day          date NOT NULL,
    subject_code text NOT NULL,
    subject      text NOT NULL,
    teacher      text NOT NULL,
    content      text NOT NULL,
    is_empty     boolean NOT NULL,
    documents    jsonb NOT NULL DEFAULT '[]',
    fingerprint  text NOT NULL,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, student_id, source_id)
);
CREATE INDEX lessons_day_idx ON school.lessons (student_id, day);

CREATE TABLE school.homework (
    source                text NOT NULL,
    student_id            text NOT NULL,
    source_id             text NOT NULL,
    subject_code          text NOT NULL,
    subject               text NOT NULL,
    given_on              date,
    due_on                date NOT NULL,
    text                  text NOT NULL,
    done                  boolean NOT NULL,
    flagged_as_assessment boolean NOT NULL,
    documents             jsonb NOT NULL DEFAULT '[]',
    -- Filled by the detection rules (T06), recomputed at each collection.
    is_assessment         text NOT NULL DEFAULT 'unknown',  -- yes | no | unknown
    assessment_reason     text NOT NULL DEFAULT '',         -- flag | keywords | ''
    fingerprint           text NOT NULL,
    first_seen            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, student_id, source_id)
);
CREATE INDEX homework_due_idx ON school.homework (student_id, due_on);

CREATE TABLE school.grades (
    source       text NOT NULL,
    student_id   text NOT NULL,
    source_id    text NOT NULL,
    subject_code text NOT NULL,
    subject      text NOT NULL,
    day          date NOT NULL,
    kind         text NOT NULL,
    title        text NOT NULL,
    raw_value    text NOT NULL,
    value        numeric,
    out_of       numeric,
    coefficient  numeric,
    period       text NOT NULL,
    fingerprint  text NOT NULL,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, student_id, source_id)
);
