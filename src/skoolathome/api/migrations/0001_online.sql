-- Online database (Neon). Only what the tablet needs: no surname, no raw school data.

CREATE TABLE devices (
    id           text PRIMARY KEY,
    role         text NOT NULL CHECK (role IN ('parent', 'child')),
    label        text NOT NULL,
    session_hash text NOT NULL UNIQUE,  -- sha256 of the cookie value: the value itself is never stored
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at   timestamptz
);

CREATE TABLE pairing_tokens (
    token_hash text PRIMARY KEY,
    role       text NOT NULL CHECK (role IN ('parent', 'child')),
    label      text NOT NULL,
    created_by text NOT NULL REFERENCES devices (id),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz
);

CREATE TABLE sheets (
    id           text PRIMARY KEY,
    student      text NOT NULL,
    subject      text NOT NULL,
    title        text NOT NULL,
    type         text NOT NULL,
    visible_from date NOT NULL,
    due_by       date,
    body         jsonb NOT NULL,  -- the whole validated Sheet
    published_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sheets_visible_idx ON sheets (visible_from);

CREATE TABLE attempts (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id   text NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    item_id    text NOT NULL,
    device_id  text NOT NULL REFERENCES devices (id),
    mode       text NOT NULL CHECK (mode IN ('solo', 'together')),
    response   jsonb NOT NULL,
    correct    boolean NOT NULL,   -- recomputed by the server
    hints_used integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attempts_sheet_idx ON attempts (sheet_id, item_id);

CREATE TABLE reports (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id   text NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    item_id    text,
    device_id  text NOT NULL REFERENCES devices (id),
    reason     text NOT NULL CHECK (reason IN ('wrong_answer', 'not_covered', 'unclear', 'too_hard', 'too_easy')),
    comment    text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
