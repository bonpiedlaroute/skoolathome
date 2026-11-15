-- T22: each answer and each report carries the trace of the generation that made its sheet,
-- so the laptop attaches it to the right Langfuse trace.

ALTER TABLE attempts ADD COLUMN trace_id text;
ALTER TABLE reports ADD COLUMN trace_id text;
