-- How long the parent wants a requested sheet to last (5 to 60 minutes): from 20 minutes, a long
-- sheet, written in parts, to prepare a test. NULL: the format's default.
ALTER TABLE agent.requests ADD COLUMN minutes integer CHECK (minutes BETWEEN 5 AND 60);
