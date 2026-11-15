-- T18: course documents (drop folder now, École Directe attachments in T25), their pages and
-- the vectors that link a lesson to the right pages (ARCHITECTURE.md §5.4).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS material;

CREATE TABLE material.documents (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text NOT NULL,              -- manual | ecoledirecte
    source_ref      text NOT NULL,              -- path under data/manual/, or the attachment id
    title           text NOT NULL,
    subject_code    text,                       -- from the folder name; NULL: any subject
    sha256          text NOT NULL,              -- a changed file is indexed again
    first_page      integer NOT NULL DEFAULT 1, -- printed number of the first page
    page_count      integer NOT NULL DEFAULT 0,
    ocr             boolean NOT NULL DEFAULT false,
    status          text NOT NULL CHECK (status IN ('indexed', 'failed', 'unsupported')),
    error           text NOT NULL DEFAULT '',
    embedding_model text NOT NULL DEFAULT '',
    indexed_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_ref)
);

CREATE TABLE material.pages (
    document_id bigint NOT NULL REFERENCES material.documents (id) ON DELETE CASCADE,
    number      integer NOT NULL,  -- printed page number: what « manuel p. 86 » cites
    text        text NOT NULL,
    ocr         boolean NOT NULL,
    PRIMARY KEY (document_id, number)
);

-- Pages are cut into pieces of about a paragraph, each with its vector. 1024 dimensions: the
-- embedding model of models.toml (bge-m3). Another model may need another migration.
CREATE TABLE material.chunks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL,
    page        integer NOT NULL,
    position    integer NOT NULL,
    text        text NOT NULL,
    embedding   vector(1024) NOT NULL,
    FOREIGN KEY (document_id, page) REFERENCES material.pages (document_id, number) ON DELETE CASCADE
);
CREATE INDEX chunks_document_idx ON material.chunks (document_id);
