-- The PWA's home page (computed after each collection): what was sent, and from which collection.
CREATE TABLE agent.home_sends (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id  bigint,                          -- the latest collection it comes from
    sent_at timestamptz NOT NULL DEFAULT now(),
    body    jsonb NOT NULL
);

-- T32: the journal of the agent's changes. A prompt promoted or rolled back, a model, an engine or a
-- quantization changed: what, why, the make eval behind it, the versions before and after.
CREATE TABLE agent.changes (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL CHECK (kind IN ('prompt', 'rollback', 'config')),
    name       text NOT NULL,              -- the prompt (sheet-generator), or the configuration part (generator, engine…)
    before     text NOT NULL DEFAULT '',   -- « v4 », « ministral-3:8b Q4_K_M »
    after      text NOT NULL DEFAULT '',
    why        text NOT NULL DEFAULT '',   -- the review, the reports: the problem it answers
    eval       jsonb NOT NULL DEFAULT '{}', -- the make eval runs behind it: id, family, passed, blocking measures
    command    text NOT NULL DEFAULT ''    -- how to undo it
);
CREATE INDEX changes_at_idx ON agent.changes (at);

-- A wrong answer reported on a version promoted less than two weeks ago: notified once.
CREATE TABLE agent.change_alerts (
    report_id bigint PRIMARY KEY,
    change_id bigint NOT NULL REFERENCES agent.changes (id),
    sent_at   timestamptz NOT NULL DEFAULT now()
);
