-- T28: rules the agent proposes from the teachers' habits it sees in the data. A proposal is an
-- instruction of the agent (same graph, same pause, same parent view), with the facts behind it.

ALTER TABLE agent.instructions DROP CONSTRAINT instructions_origin_check;
ALTER TABLE agent.instructions ADD CONSTRAINT instructions_origin_check CHECK (origin IN ('pwa', 'cli', 'agent'));
ALTER TABLE agent.instructions
    ADD COLUMN agent_rule   jsonb,                       -- the rule the agent proposes
    ADD COLUMN facts        jsonb NOT NULL DEFAULT '[]', -- what justifies it, in French, no surname
    ADD COLUMN proposal_key text;                        -- the habit: lesson_quiz:HI-GE

-- One row per habit ever proposed: a refused proposal is never made again.
CREATE TABLE agent.rule_proposals (
    key            text PRIMARY KEY,
    subject_code   text,
    instruction_id text NOT NULL,
    status         text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'accepted', 'edited', 'rejected')),
    facts          jsonb NOT NULL DEFAULT '[]',
    rule           jsonb NOT NULL,   -- as proposed
    final          jsonb,            -- as saved (accepted or edited)
    reason         text NOT NULL DEFAULT '',  -- why the parent refused, when given
    created_at     timestamptz NOT NULL DEFAULT now(),
    decided_at     timestamptz
);
