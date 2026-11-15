-- T21: the parent's instructions in plain French. The laptop reads them with the feedback, and
-- sends back its question or its proposal (« J'ai compris : … ») in its next signed batch. The
-- parent's replies go the same way. The rules table is the laptop's list, replaced by each batch.

CREATE TABLE instructions (
    id           text PRIMARY KEY,
    text         text NOT NULL DEFAULT '',  -- '' for a rule written in the form
    rule         jsonb,                     -- the form: saved as it is by the laptop
    created_by   text NOT NULL REFERENCES devices (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'question', 'proposed', 'saved', 'rejected', 'failed')),
    question     text NOT NULL DEFAULT '',
    summary      text NOT NULL DEFAULT '',
    proposal     jsonb,
    rule_id      text,
    rule_version integer,
    reason       text NOT NULL DEFAULT '',
    waiting      boolean NOT NULL DEFAULT false,  -- the parent replied; the laptop has not read it yet
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX instructions_created_idx ON instructions (created_at);

CREATE TABLE instruction_replies (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instruction_id text NOT NULL REFERENCES instructions (id) ON DELETE CASCADE,
    kind           text NOT NULL CHECK (kind IN ('answer', 'approve', 'edit', 'reject')),
    text           text NOT NULL DEFAULT '',
    rule           jsonb,
    created_by     text NOT NULL REFERENCES devices (id),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX instruction_replies_created_idx ON instruction_replies (created_at);

CREATE TABLE rules (
    id          text PRIMARY KEY,
    version     integer NOT NULL,
    status      text NOT NULL,
    summary     text NOT NULL,  -- the rule in one French sentence, written by the laptop
    body        jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
