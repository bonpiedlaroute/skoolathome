-- T24: the weekly review sent by the laptop with its batch, and the parent's annotations of the
-- sheets it proposes. The laptop reads the annotations back with the feedback.

CREATE TABLE weekly_reviews (
    week        text PRIMARY KEY,  -- ISO week: 2026-W46
    body        jsonb NOT NULL,    -- indicators and sheets to annotate: counts and rates, no lesson text
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE annotations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id   text NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    item_id    text NOT NULL,
    device_id  text NOT NULL REFERENCES devices (id),
    ok         boolean NOT NULL,
    problem    text CHECK (problem IN ('faithful', 'answer_correct', 'level_ok', 'useful')),
    comment    text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),  -- the latest change: the laptop reads it again
    UNIQUE (sheet_id, item_id)
);
CREATE INDEX annotations_created_idx ON annotations (created_at);
