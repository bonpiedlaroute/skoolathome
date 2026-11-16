-- The home page: what comes next, the latest grades, the sheets to do (schema/home.py). One row,
-- replaced after each collection on the laptop. Dated data: the PWA picks « tomorrow » itself.
CREATE TABLE home (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    body        jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
