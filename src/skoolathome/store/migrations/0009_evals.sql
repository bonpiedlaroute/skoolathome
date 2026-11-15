-- T23: the reference set and every run of `make eval`.
--
-- The cases are kept here as well as in the Langfuse datasets: Langfuse can be emptied without
-- losing them, and `make eval` also runs without it.

-- What the model read for a sheet: the task, the sources, the context. A real case of the
-- reference set replays exactly that.
ALTER TABLE agent.sheets ADD COLUMN model_input jsonb;

CREATE TABLE agent.eval_cases (
    id         text PRIMARY KEY,  -- also the id of the Langfuse dataset item
    family     text NOT NULL CHECK (family IN ('sheets', 'tests')),
    origin     text NOT NULL CHECK (origin IN ('real', 'trap', 'report')),
    input      jsonb NOT NULL,
    expected   jsonb NOT NULL,
    metadata   jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    synced_at  timestamptz  -- sent to the Langfuse dataset; NULL: to send again
);

CREATE TABLE agent.eval_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    family      text NOT NULL,
    label       text NOT NULL,              -- production | staging: the prompt versions used
    config      jsonb NOT NULL,             -- prompt versions, models, quantization, engine, git commit
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    cases       integer NOT NULL DEFAULT 0,
    metrics     jsonb NOT NULL DEFAULT '{}',
    results     jsonb NOT NULL DEFAULT '[]',  -- one per case: what came out, and its measures
    passed      boolean,                    -- every blocking threshold held
    experiment  text NOT NULL DEFAULT ''    -- the Langfuse experiment run, to compare in its interface
);
CREATE INDEX eval_runs_family_idx ON agent.eval_runs (family, label, finished_at);
