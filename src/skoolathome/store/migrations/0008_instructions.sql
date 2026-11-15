-- T21: the parent's instructions in plain French, the replies to the agent's questions and
-- proposals, and every [Modifier]: a real interpretation error, a future test case (T31).
-- The graph of instructions keeps its own checkpoints in the langgraph schema.

CREATE TABLE agent.instructions (
    id           text PRIMARY KEY,   -- the id online (ins-…), or cli-…
    student_id   text,
    text         text NOT NULL DEFAULT '',  -- the sentence; '' for a rule written in the form
    form_rule    jsonb,                     -- the form: saved as it is, without the model
    origin       text NOT NULL CHECK (origin IN ('pwa', 'cli')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'question', 'proposed', 'saved', 'rejected', 'failed')),
    question     text NOT NULL DEFAULT '',
    summary      text NOT NULL DEFAULT '',  -- « J'ai compris : … », written by the code
    proposal     jsonb,
    rule_id      text,
    rule_version integer,
    reason       text NOT NULL DEFAULT '',
    trace_id     text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    reported_at  timestamptz  -- made online: its new state sent back with a batch
);

CREATE TABLE agent.instruction_replies (
    id             text PRIMARY KEY,  -- pwa-<id online>, or cli-…
    instruction_id text NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('answer', 'approve', 'edit', 'reject')),
    text           text NOT NULL DEFAULT '',
    rule           jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    handled_at     timestamptz
);
CREATE INDEX instruction_replies_waiting_idx ON agent.instruction_replies (instruction_id) WHERE handled_at IS NULL;

CREATE TABLE agent.instruction_corrections (
    instruction_id text PRIMARY KEY,
    text           text NOT NULL,   -- the parent's sentence (and the answers to the questions)
    proposed       jsonb NOT NULL,  -- the rule the agent understood
    final          jsonb NOT NULL,  -- the rule the parent saved
    created_at     timestamptz NOT NULL DEFAULT now()
);
