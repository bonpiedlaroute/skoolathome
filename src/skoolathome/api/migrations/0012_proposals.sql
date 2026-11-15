-- T28: an instruction can come from the agent: a rule it proposes, with the facts behind it.
-- It is created by the laptop's batch, so it has no device.

ALTER TABLE instructions ALTER COLUMN created_by DROP NOT NULL;
ALTER TABLE instructions
    ADD COLUMN origin text NOT NULL DEFAULT 'parent' CHECK (origin IN ('parent', 'agent')),
    ADD COLUMN facts  jsonb NOT NULL DEFAULT '[]';
