-- T10: together mode (the parent rates the answer), offline answers sent later.

-- An oral answer has no typed response: the parent's rating stands for it.
ALTER TABLE attempts ALTER COLUMN response DROP NOT NULL;
ALTER TABLE attempts ADD COLUMN rating text CHECK (rating IN ('good', 'partial', 'bad'));
-- Set by the browser: an answer sent twice after a network failure is stored once.
ALTER TABLE attempts ADD COLUMN client_id text;
-- When the answer was given, which can be long before it reaches the server.
ALTER TABLE attempts ADD COLUMN answered_at timestamptz;
CREATE UNIQUE INDEX attempts_client_id_idx ON attempts (client_id) WHERE client_id IS NOT NULL;

ALTER TABLE reports ADD COLUMN client_id text;
CREATE UNIQUE INDEX reports_client_id_idx ON reports (client_id) WHERE client_id IS NOT NULL;
