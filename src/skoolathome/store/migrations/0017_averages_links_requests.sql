-- The PWA's averages: a grade École Directe marks « non significative » is shown, not counted.
-- The grades already collected get it from the raw responses, without calling École Directe.
ALTER TABLE school.grades ADD COLUMN significant boolean NOT NULL DEFAULT true;

WITH latest AS (
    SELECT DISTINCT ON (note->>'id') note->>'id' AS source_id, note
    FROM raw.responses r CROSS JOIN LATERAL jsonb_array_elements(r.body->'data'->'notes') AS note
    WHERE r.kind = 'grades' AND r.source = 'ecoledirecte'
    ORDER BY note->>'id', r.fetched_at DESC
)
UPDATE school.grades g SET significant = false
FROM latest
WHERE g.source = 'ecoledirecte' AND g.source_id = latest.source_id
  AND lower(coalesce(latest.note->>'nonSignificatif', 'false')) = 'true';

-- The web pages a teacher linked in a lesson (GeoGebra, videos…), for the PWA. Out of the lesson's
-- fingerprint: the models do not read them. Filled for the lessons already collected by
-- `skoolathome db migrate` (collectors/ecoledirecte/backfill.py), from the raw responses.
ALTER TABLE school.lessons ADD COLUMN links jsonb NOT NULL DEFAULT '[]';

-- Every attached file is now downloaded, whatever its format, so that the PWA can offer it; only
-- the PDFs are indexed. The files set aside for their format are taken again, and the lessons of
-- the last six weeks collected before T25 get their files listed.
UPDATE school.attachments SET status = 'pending' WHERE status = 'unsupported' AND attempts = 0 AND error = '';

INSERT INTO school.attachments (source, student_id, lesson_source_id, document_id, name, kind, size, status)
SELECT l.source, l.student_id, l.source_id, d->>'source_id', d->>'name', d->>'kind', (d->>'size')::bigint,
       CASE WHEN coalesce((d->>'size')::bigint, 0) > 20 * 1024 * 1024 THEN 'too_large' ELSE 'pending' END
FROM school.lessons l CROSS JOIN LATERAL jsonb_array_elements(l.documents) AS d
WHERE l.day >= current_date - 42 AND coalesce(d->>'source_id', '') <> ''
ON CONFLICT DO NOTHING;

-- A sheet asked for from the PWA is made by the next `skoolathome explain` pass (every five minutes
-- while the computer is on), three tries at most: then it waits for the evening.
CREATE TABLE agent.request_tries (
    id         text PRIMARY KEY,          -- the request's id online (pwa-…)
    tries      integer NOT NULL DEFAULT 0,
    last_at    timestamptz,
    last_error text NOT NULL DEFAULT '',
    gave_up_at timestamptz                -- the parent was told it waits for the evening
);
