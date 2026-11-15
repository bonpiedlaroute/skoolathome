-- T17: the end-of-evening report sent by the laptop (NIP-98), pushed to the parent devices.
-- details: counts and abstention reasons, for the parent view (no lesson text).

CREATE TABLE evening_reports (
    run_date    date PRIMARY KEY,
    status      text NOT NULL CHECK (status IN ('ok', 'ko')),
    title       text NOT NULL,
    body        text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    received_at timestamptz NOT NULL DEFAULT now()
);
