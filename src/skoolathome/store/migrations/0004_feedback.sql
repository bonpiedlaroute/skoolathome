-- T17: what came back from the tablet, kept on the laptop (ARCHITECTURE.md §6), the judge's
-- verdicts, and one row per evening run.

CREATE SCHEMA IF NOT EXISTS feedback;

-- Where the next GET /api/feedback starts: the `until` of the previous call, in microseconds.
CREATE TABLE feedback.cursor (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),  -- a single row
    since      bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- trace_id: the trace of the sheet, when the agent made it. T22 sends the scores to Langfuse,
-- including those received before then (sent_to_langfuse_at still NULL).
CREATE TABLE feedback.attempts (
    id                  bigint PRIMARY KEY,  -- the id online: received twice, stored once
    sheet_id            text NOT NULL,
    item_id             text NOT NULL,
    role                text NOT NULL,       -- parent | child: the device that answered
    mode                text NOT NULL,       -- solo | together
    response            jsonb,
    correct             boolean NOT NULL,
    rating              text,                -- together mode: good | partial | bad
    hints_used          integer NOT NULL DEFAULT 0,
    answered_at         timestamptz,
    created_at          timestamptz NOT NULL,
    trace_id            text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    sent_to_langfuse_at timestamptz
);
CREATE INDEX feedback_attempts_sheet_idx ON feedback.attempts (sheet_id, item_id);

CREATE TABLE feedback.reports (
    id                  bigint PRIMARY KEY,
    sheet_id            text NOT NULL,
    item_id             text,
    role                text NOT NULL,
    reason              text NOT NULL,       -- wrong_answer | not_covered | unclear | too_hard | too_easy
    comment             text NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL,
    trace_id            text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    sent_to_langfuse_at timestamptz
);

CREATE TABLE feedback.finishes (
    id                  text PRIMARY KEY,    -- the client id of that run of the sheet
    sheet_id            text NOT NULL,
    role                text NOT NULL,
    score               integer NOT NULL,
    total               integer NOT NULL,
    to_review           jsonb NOT NULL DEFAULT '[]',
    finished_at         timestamptz NOT NULL,
    trace_id            text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    sent_to_langfuse_at timestamptz
);

-- The judge's last verdict on a sheet (the second model, another family).
ALTER TABLE agent.sheets ADD COLUMN judge jsonb;

-- One row per evening: what the end-of-run report said (sent by Web Push).
CREATE TABLE agent.runs (
    run_date    date PRIMARY KEY,
    status      text NOT NULL CHECK (status IN ('ok', 'ko')),
    title       text NOT NULL,
    body        text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    finished_at timestamptz NOT NULL DEFAULT now()
);
