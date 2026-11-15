-- T29: the explanations asked from the tablet (« Je n'ai pas compris »), as the laptop made them.

CREATE TABLE agent.explanations (
    id                  text PRIMARY KEY,  -- the id online
    sheet_id            text NOT NULL,
    item_id             text NOT NULL,
    question            text NOT NULL DEFAULT '',
    status              text NOT NULL CHECK (status IN ('done', 'abstained')),
    explanation         jsonb,
    reason              text NOT NULL DEFAULT '',
    attempts            integer NOT NULL DEFAULT 0,
    guards              jsonb NOT NULL DEFAULT '[]',
    trace_id            text NOT NULL,     -- the explanation's own trace; « Ça m'a aidé ? » goes there
    model               text NOT NULL DEFAULT '',
    prompt_version      text NOT NULL DEFAULT '',
    asked_at            timestamptz,
    answered_at         timestamptz NOT NULL DEFAULT now(),
    helped              boolean,           -- from the feedback
    helped_at           timestamptz,
    sent_to_langfuse_at timestamptz
);
CREATE INDEX explanations_sheet_idx ON agent.explanations (sheet_id, item_id);
