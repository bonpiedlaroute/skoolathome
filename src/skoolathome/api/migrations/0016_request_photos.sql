-- Photos of the notebook sent with a request: the course as written in class, which École Directe
-- does not have (a printed handout completed by hand, its boxes left blank in the teacher's PDF).
--
-- A request with photos goes through: photos (the PWA sends them) → reading (the laptop reads
-- them) → to_review (the parent checks the text) → pending (the sheet is written) → done/abstained.
-- The photos stay online only until the laptop has read them.

ALTER TABLE sheet_requests DROP CONSTRAINT sheet_requests_status_check;
ALTER TABLE sheet_requests ADD CONSTRAINT sheet_requests_status_check
    CHECK (status IN ('photos', 'reading', 'to_review', 'pending', 'done', 'abstained'));

-- How many photos the PWA announced (0: none, the request is pending at once).
ALTER TABLE sheet_requests ADD COLUMN photos integer NOT NULL DEFAULT 0 CHECK (photos BETWEEN 0 AND 6);
-- The text of each photo: [{"text": "…", "problem": "…"}], read by the laptop, then corrected by the parent.
ALTER TABLE sheet_requests ADD COLUMN pages jsonb;
-- When the request became pending: the laptop picks it up with the feedback from then on.
ALTER TABLE sheet_requests ADD COLUMN ready_at timestamptz;
UPDATE sheet_requests SET ready_at = created_at;
CREATE INDEX sheet_requests_ready_idx ON sheet_requests (ready_at);

CREATE TABLE request_photos (
    request_id  text NOT NULL REFERENCES sheet_requests (id) ON DELETE CASCADE,
    position    integer NOT NULL CHECK (position BETWEEN 1 AND 6),
    body        bytea NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, position)
);
