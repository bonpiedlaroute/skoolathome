-- T30: the speed of each engine on this laptop, next to the quality measured by make eval
-- (agent.eval_runs), for the comparison table.

CREATE TABLE agent.engine_benchmarks (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measured_at           timestamptz NOT NULL DEFAULT now(),
    engine                text NOT NULL,     -- ollama, llamacpp-vulkan, llamacpp-cpu, openvino-gpu, openvino-npu
    role                  text NOT NULL,     -- generator | judge
    model                 text NOT NULL,
    quantization          text NOT NULL,
    prompt_tokens_per_s   real NOT NULL,     -- reading
    generated_tokens_per_s real NOT NULL,    -- writing
    load_seconds          real,              -- Ollama only: other servers load their model at start
    model_bytes           bigint,
    system_used_bytes     bigint,            -- RAM used by the whole system during the measure
    joules_per_token      real,              -- when the processor's energy counter can be read
    total_seconds         real NOT NULL,
    release               text NOT NULL DEFAULT ''
);
