-- The files the teachers attached to the lessons of the home page, put online by the laptop
-- (home.upload_files): the child opens them from the PWA. Only the files of the latest page are
-- kept; a new page removes the others. Named by their sha256: the same file is sent once.
CREATE TABLE documents (
    sha256      text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    name        text NOT NULL,
    media_type  text NOT NULL,
    size        integer NOT NULL,
    body        bytea NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

-- Keys the API makes for itself, e.g. to sign the links to the documents: a link opened outside
-- the installed app (Safari on the iPad has its own cookies) works for two days, without a session.
CREATE TABLE app_keys (
    name       text PRIMARY KEY,
    key        bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
