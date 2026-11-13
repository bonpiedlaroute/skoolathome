-- Schemas and extensions. Tables come with the task that needs them.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw;     -- raw responses of the sources, as received
CREATE SCHEMA IF NOT EXISTS school;  -- common format (ARCHITECTURE.md §5.2)
