-- T13: the laptop publishes and reads feedback with NIP-98 signed requests.

-- Every accepted event id, so that a captured request cannot be played twice.
-- Rows older than a day are purged: past 60 seconds an event is refused anyway.
CREATE TABLE nip98_seen (
    event_id text PRIMARY KEY,
    seen_at  timestamptz NOT NULL DEFAULT now()
);

-- GET /api/feedback reads by time.
CREATE INDEX attempts_created_idx ON attempts (created_at);
CREATE INDEX reports_created_idx ON reports (created_at);
CREATE INDEX sheet_finishes_finished_idx ON sheet_finishes (finished_at);
