-- Core backend schema and tables
-- Extracted from app/utils/postgres.py for DB initialization

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS backend;
GRANT USAGE, CREATE ON SCHEMA backend TO aiauser;

CREATE TABLE IF NOT EXISTS backend.users (
    user_id    TEXT        PRIMARY KEY,
    email      TEXT        NOT NULL UNIQUE,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO backend.users (user_id, email, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'guest@aia.local', 'Guest User')
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS backend.document_uploads (
    doc_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    template_type     TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    file_name         TEXT        NOT NULL,
    status            TEXT        NOT NULL,
    uploaded_ts       TIMESTAMPTZ NOT NULL,
    processed_ts      TIMESTAMPTZ,
    status_updated_at TIMESTAMPTZ,
    result            JSONB,
    result_md         TEXT,
    error_message     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_filename
    ON backend.document_uploads (user_id, file_name);

CREATE TABLE IF NOT EXISTS backend.cost_usage (
    doc_id         UUID             NOT NULL REFERENCES backend.document_uploads(doc_id) ON DELETE CASCADE,
    agent_name     VARCHAR(50)      NOT NULL,
    input_tokens   INT              NOT NULL,
    output_tokens  INT              NOT NULL,
    total_cost_usd DOUBLE PRECISION NOT NULL
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'backend'
          AND table_name = 'cost_usage'
          AND column_name = 'unit_cost'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'backend'
          AND table_name = 'cost_usage'
          AND column_name = 'total_cost_usd'
    ) THEN
        ALTER TABLE backend.cost_usage
        RENAME COLUMN unit_cost TO total_cost_usd;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_cost_usage_doc_id ON backend.cost_usage(doc_id);
CREATE INDEX IF NOT EXISTS idx_cost_usage_doc_agent ON backend.cost_usage(doc_id, agent_name);
