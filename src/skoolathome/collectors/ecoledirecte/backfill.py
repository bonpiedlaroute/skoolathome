"""What a new mapping adds to the data already collected, from the raw responses kept since T05:
no call to École Directe. Run once by `skoolathome db migrate`, right after the migration that
adds the column.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.collectors.ecoledirecte import mapping


def lesson_links(conn: psycopg.Connection) -> int:
    """The links of every lesson, from its latest diary response (0017). Returns the lessons that got some."""
    rows = conn.execute(
        "SELECT DISTINCT ON (m->>'id', r.body->'data'->>'date') m->>'id', m->'contenuDeSeance'->>'contenu' "
        "FROM raw.responses r CROSS JOIN LATERAL jsonb_array_elements(r.body->'data'->'matieres') AS m "
        "WHERE r.kind = 'diary' AND r.source = %s AND coalesce(m->'contenuDeSeance'->>'contenu', '') <> '' "
        "ORDER BY m->>'id', r.body->'data'->>'date', r.fetched_at DESC",
        (mapping.SOURCE,),
    ).fetchall()
    updated = 0
    for source_id, encoded in rows:
        try:
            links = mapping.links_of(encoded)
        except ValueError:  # not base64: nothing to take from it
            continue
        if links:
            updated += conn.execute(
                "UPDATE school.lessons SET links = %s WHERE source = %s AND source_id = %s",
                (Jsonb([link.model_dump() for link in links]), mapping.SOURCE, source_id),
            ).rowcount
    return updated
