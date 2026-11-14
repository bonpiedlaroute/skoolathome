-- T15 and T16: what the evening graph produces. LangGraph's own tables go in their schema.

CREATE SCHEMA IF NOT EXISTS agent;      -- summaries, sheets, abstentions (ARCHITECTURE.md §6)
CREATE SCHEMA IF NOT EXISTS langgraph;  -- checkpoints (PostgresSaver creates its tables there)

-- Each lesson is summarized once, when it arrives. A lesson edited afterwards by the teacher
-- has a new fingerprint, hence a new summary.
CREATE TABLE agent.lesson_summaries (
    source         text NOT NULL,
    student_id     text NOT NULL,
    source_id      text NOT NULL,
    fingerprint    text NOT NULL,
    summary        text NOT NULL,
    model          text NOT NULL,  -- 'none' when the lesson is short enough to be its own summary
    prompt_version text NOT NULL DEFAULT '',
    trace_id       text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, student_id, source_id, fingerprint)
);

-- One row per task of an evening: a sheet ready to publish, or an abstention and its reason.
CREATE TABLE agent.sheets (
    id             text PRIMARY KEY,  -- also the id online
    run_date       date NOT NULL,
    task_id        text NOT NULL,
    student_id     text NOT NULL,
    subject_code   text NOT NULL,
    kind           text NOT NULL,     -- exercises | lesson_recap | quick_quiz | test_revision
    status         text NOT NULL CHECK (status IN ('ready', 'abstained', 'published')),
    body           jsonb,             -- the validated Sheet; NULL for an abstention
    reason         text NOT NULL DEFAULT '',  -- abstention: why, in French, shown to the parent
    attempts       integer NOT NULL,  -- generations made: 1, or more after a guard failed
    guards         jsonb NOT NULL DEFAULT '[]',  -- every guard result of every attempt
    trace_id       text NOT NULL,     -- the Langfuse trace: feedback is attached to it later
    model          text NOT NULL,
    prompt_version text NOT NULL,
    release        text NOT NULL,     -- git commit of the code that made it
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    published_at   timestamptz
);
CREATE INDEX sheets_run_idx ON agent.sheets (run_date);
