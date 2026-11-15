-- T24: the weekly review, and the parent's annotations of the sheets it proposes.

-- The parent's verdict on each question of an annotated sheet, copied from the API.
CREATE TABLE feedback.annotations (
    id                  bigint PRIMARY KEY,  -- the id online
    sheet_id            text NOT NULL,
    item_id             text NOT NULL,
    ok                  boolean NOT NULL,
    problem             text,                -- faithful | answer_correct | level_ok | useful
    comment             text NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL,
    received_at         timestamptz NOT NULL DEFAULT now(),
    sent_to_langfuse_at timestamptz
);
CREATE INDEX feedback_annotations_sheet_idx ON feedback.annotations (sheet_id, item_id);

CREATE TABLE agent.weekly_reviews (
    week       text PRIMARY KEY,   -- ISO week: 2026-W46
    start_day  date NOT NULL,
    end_day    date NOT NULL,
    body       jsonb NOT NULL,     -- the WeeklyReview sent online
    metrics    jsonb NOT NULL,     -- every number, for the next reviews (T32 compares versions)
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz         -- went out with a batch
);
