-- Sheets asked for by the parent: from the PWA (copied by sync_feedback) or from the laptop
-- (`skoolathome ask`). Handled first at the next run, outside the evening load.

CREATE TABLE agent.requests (
    id           text PRIMARY KEY,
    student_id   text,
    subject_code text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('revision', 'extra')),
    focus        text NOT NULL DEFAULT '',
    items        integer,
    origin       text NOT NULL CHECK (origin IN ('pwa', 'cli')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'abstained')),
    sheet_id     text,
    reason       text NOT NULL DEFAULT '',
    handled_at   timestamptz,
    reported_at  timestamptz  -- a request made online: its outcome sent back with a batch
);
