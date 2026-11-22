-- The text of the photos of the notebook sent with a request, as the parent checked it: the first
-- source of the sheet (the course as written in class, which École Directe does not have).
ALTER TABLE agent.requests ADD COLUMN pages jsonb NOT NULL DEFAULT '[]';
