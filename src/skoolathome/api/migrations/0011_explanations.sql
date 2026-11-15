-- T29: « Je n'ai pas compris ». The tablet asks; the laptop polls this queue (NIP-98) while it is
-- on, and sends the explanation back. « Ça m'a aidé ? » comes back with the feedback.

CREATE TABLE explanations (
    id          text PRIMARY KEY,
    sheet_id    text NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    item_id     text NOT NULL,
    device_id   text NOT NULL REFERENCES devices (id),
    question    text NOT NULL DEFAULT '',  -- what is not clear, in the student's words
    status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'abstained')),
    explanation jsonb,                     -- text, example, quote: no lesson beyond the short quote
    reason      text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz,
    helped      boolean,
    helped_at   timestamptz
);
CREATE INDEX explanations_pending_idx ON explanations (created_at) WHERE status = 'pending';
CREATE INDEX explanations_helped_idx ON explanations (helped_at);
