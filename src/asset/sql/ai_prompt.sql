CREATE TABLE IF NOT EXISTS ai_prompt /* AI middleware storage */ (
    id INTEGER PRIMARY KEY /* SQLite row ID */,
    prompt_key TEXT NOT NULL /* Registered task prompt identity */,
    version INTEGER NOT NULL CHECK (version > 0) /* Immutable content version */,
    instruction TEXT NOT NULL /* Editable task instruction; fixed privacy and output guards are separate */,
    note TEXT NOT NULL DEFAULT '' /* Version note */,
    state TEXT NOT NULL CHECK (state IN ('DRAFT', 'PRODUCTION', 'RETIRED')) /* DRAFT, PRODUCTION or RETIRED */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 timestamp with six fractional digits */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 timestamp with six fractional digits */,
    UNIQUE (prompt_key, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_prompt_production ON ai_prompt (prompt_key) WHERE state = 'PRODUCTION';
