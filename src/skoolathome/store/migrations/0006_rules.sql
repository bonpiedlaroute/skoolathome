-- T19 and T20: rules (versioned), the model's answer on ambiguous homework, and what the
-- evening plan needs to know about each sheet.

-- Every version is kept. The latest version of a rule is the one that counts.
CREATE TABLE agent.rules (
    id         text NOT NULL,
    version    integer NOT NULL,
    status     text NOT NULL CHECK (status IN ('proposed', 'active', 'archived')),
    body       jsonb NOT NULL,  -- the Rule, as validated by skoolathome.schema.rule
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

-- Homework that might announce a test without saying it clearly: asked to the model once per
-- version of its text, as a last resort (ARCHITECTURE.md §8).
CREATE TABLE agent.assessment_checks (
    source        text NOT NULL,
    student_id    text NOT NULL,
    source_id     text NOT NULL,
    fingerprint   text NOT NULL,
    is_assessment boolean NOT NULL,
    day           date,           -- the test date the model read, if any
    scope         text NOT NULL DEFAULT '',
    confidence    real NOT NULL,
    model         text NOT NULL,
    trace_id      text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, student_id, source_id, fingerprint)
);

ALTER TABLE agent.sheets
    ADD COLUMN rule_id      text NOT NULL DEFAULT '',
    ADD COLUMN rule_version integer NOT NULL DEFAULT 0,
    ADD COLUMN visible_from date,
    ADD COLUMN due_by       date,
    ADD COLUMN minutes      integer NOT NULL DEFAULT 0,  -- counted in the evening load
    ADD COLUMN inputs       text NOT NULL DEFAULT '';    -- fingerprint of what the sheet was made from
