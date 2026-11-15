-- Sheets asked for by the parent from the PWA. The laptop picks them up with the feedback, and
-- says what became of them in its next signed batch.

CREATE TABLE sheet_requests (
    id         text PRIMARY KEY,
    subject    text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('revision', 'extra')),
    focus      text NOT NULL DEFAULT '',
    items      integer,
    created_by text NOT NULL REFERENCES devices (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'abstained')),
    sheet_id   text,
    reason     text NOT NULL DEFAULT '',
    handled_at timestamptz
);
CREATE INDEX sheet_requests_created_idx ON sheet_requests (created_at);
