-- T11: Web Push notifications and the daily scheduled task.

CREATE TABLE push_subscriptions (
    endpoint   text PRIMARY KEY,
    device_id  text NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    p256dh     text NOT NULL,
    auth       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- One row per day: the scheduled task does its work once, even if called twice.
CREATE TABLE daily_runs (
    day     date PRIMARY KEY,
    ran_at  timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'
);

-- A sheet finished alone: notify the parent once per run, even if the browser sends it twice.
CREATE TABLE sheet_finishes (
    client_id   text PRIMARY KEY,
    sheet_id    text NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    device_id   text NOT NULL REFERENCES devices (id),
    score       integer NOT NULL,
    total       integer NOT NULL,
    to_review   jsonb NOT NULL DEFAULT '[]',
    finished_at timestamptz NOT NULL DEFAULT now()
);
