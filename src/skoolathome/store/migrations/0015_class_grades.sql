-- The class's average, lowest and highest grade on each test (the PWA's grades block). École
-- Directe gives them with every grade; the grades already collected get them from the raw
-- responses kept since T05, without calling École Directe again.

ALTER TABLE school.grades
    ADD COLUMN class_average numeric,
    ADD COLUMN class_min     numeric,
    ADD COLUMN class_max     numeric;

WITH latest AS (
    SELECT DISTINCT ON (note->>'id') note->>'id' AS source_id, note
    FROM raw.responses r CROSS JOIN LATERAL jsonb_array_elements(r.body->'data'->'notes') AS note
    WHERE r.kind = 'grades' AND r.source = 'ecoledirecte'
    ORDER BY note->>'id', r.fetched_at DESC
)
UPDATE school.grades g SET
    class_average = CASE WHEN latest.note->>'moyenneClasse' ~ '^\s*\d+([.,]\d+)?\s*$'
                         THEN replace(btrim(latest.note->>'moyenneClasse'), ',', '.')::numeric END,
    class_min     = CASE WHEN latest.note->>'minClasse' ~ '^\s*\d+([.,]\d+)?\s*$'
                         THEN replace(btrim(latest.note->>'minClasse'), ',', '.')::numeric END,
    class_max     = CASE WHEN latest.note->>'maxClasse' ~ '^\s*\d+([.,]\d+)?\s*$'
                         THEN replace(btrim(latest.note->>'maxClasse'), ',', '.')::numeric END
FROM latest
WHERE g.source = 'ecoledirecte' AND g.source_id = latest.source_id;

-- T31 and T33: the reference set gains the judge's cases (the parent's annotations), the sentences
-- written by hand, the parent's [Modifier], and the lessons annotated for the search.
ALTER TABLE agent.eval_cases DROP CONSTRAINT eval_cases_family_check;
ALTER TABLE agent.eval_cases ADD CONSTRAINT eval_cases_family_check
    CHECK (family IN ('sheets', 'tests', 'retrieval', 'instructions', 'explanations', 'judge'));
ALTER TABLE agent.eval_cases DROP CONSTRAINT eval_cases_origin_check;
ALTER TABLE agent.eval_cases ADD CONSTRAINT eval_cases_origin_check
    CHECK (origin IN ('real', 'trap', 'report', 'attachment', 'proposal', 'explanation', 'sentence', 'correction',
                      'annotated', 'annotation'));
