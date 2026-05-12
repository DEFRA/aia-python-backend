-- Data Pipeline schema and tables
-- Applied automatically when the Podman postgres container first starts.

CREATE SCHEMA IF NOT EXISTS data_pipeline;

-- ---------------------------------------------------------------------------
-- Reference: allowed policy categories (managed data; can evolve over time)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.policy_source_categories (
    category   TEXT PRIMARY KEY,
    description TEXT,
    isactive   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Migration: rename legacy table if it exists under the old name
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'data_pipeline' AND table_name = 'source_path_policydoc'
    ) THEN
        ALTER TABLE data_pipeline.source_path_policydoc RENAME TO source_policy_docs;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: source_policy_docs — rename desp → filename, type → source, drop datasize
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'desp'
    ) THEN
        ALTER TABLE data_pipeline.source_policy_docs RENAME COLUMN desp TO filename;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'type'
    )
    AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'source'
    ) THEN
        ALTER TABLE data_pipeline.source_policy_docs RENAME COLUMN type TO source;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'datasize'
    ) THEN
        ALTER TABLE data_pipeline.source_policy_docs DROP COLUMN datasize;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: policy_document_sync — drop file_name, add content_size
-- (Skipped on a fresh DB: the table is created later in this script with the
-- final shape, so there is nothing to migrate.)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'data_pipeline' AND table_name = 'policy_document_sync'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'data_pipeline'
              AND table_name   = 'policy_document_sync'
              AND column_name  = 'file_name'
        ) THEN
            ALTER TABLE data_pipeline.policy_document_sync DROP COLUMN file_name;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'data_pipeline'
              AND table_name   = 'policy_document_sync'
              AND column_name  = 'content_size'
        ) THEN
            ALTER TABLE data_pipeline.policy_document_sync ADD COLUMN content_size INTEGER;
        END IF;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: move category from question_categories to policy_documents
-- (Skipped on a fresh DB: policy_documents is created below with category
-- already declared NOT NULL.)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'data_pipeline' AND table_name = 'policy_documents'
    ) THEN
        -- Add category column to policy_documents if not already present
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'data_pipeline'
              AND table_name   = 'policy_documents'
              AND column_name  = 'category'
        ) THEN
            ALTER TABLE data_pipeline.policy_documents ADD COLUMN category TEXT;
            -- Back-fill from question_categories via the most common category per document
            UPDATE data_pipeline.policy_documents pd
            SET    category = (
                SELECT qc.category
                FROM   data_pipeline.question_categories qc
                JOIN   data_pipeline.questions q ON q.question_id = qc.question_id
                WHERE  q.policy_doc_id = pd.policy_doc_id
                GROUP  BY qc.category
                ORDER  BY COUNT(*) DESC
                LIMIT  1
            );
            ALTER TABLE data_pipeline.policy_documents ALTER COLUMN category SET NOT NULL;
        END IF;
    END IF;
    -- Drop the junction table once data is migrated (no-op on fresh DB)
    DROP TABLE IF EXISTS data_pipeline.question_categories;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: ensure source_url unique constraint exists on policy_documents
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema    = 'data_pipeline'
          AND table_name      = 'policy_documents'
          AND constraint_type = 'UNIQUE'
          AND constraint_name = 'policy_documents_source_url_key'
    ) THEN
        ALTER TABLE data_pipeline.policy_documents ADD CONSTRAINT policy_documents_source_url_key UNIQUE (source_url);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Source: active policy URLs to process
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.source_policy_docs (
    url_id   SERIAL PRIMARY KEY,
    url      TEXT    NOT NULL UNIQUE,
    filename TEXT    NOT NULL,
    category TEXT    NOT NULL
        REFERENCES data_pipeline.policy_source_categories(category),
    source   TEXT    NOT NULL DEFAULT 'SharePoint',
    CONSTRAINT source_policy_docs_source_check
        CHECK (source IN ('SharePoint', 'Confluence', 'GitHub')),
    isactive BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NULL
);

-- ---------------------------------------------------------------------------
-- Migration: enforce source/category integrity on existing source_policy_docs
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- Backfill categories from existing rows before applying FK.
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'data_pipeline' AND table_name = 'source_policy_docs'
    ) THEN
        INSERT INTO data_pipeline.policy_source_categories (category)
        SELECT DISTINCT spd.category
        FROM data_pipeline.source_policy_docs spd
        WHERE spd.category IS NOT NULL
        ON CONFLICT (category) DO NOTHING;
    END IF;

    -- Add category FK if missing.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'category'
    )
    AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema    = 'data_pipeline'
          AND table_name      = 'source_policy_docs'
          AND constraint_type = 'FOREIGN KEY'
          AND constraint_name = 'source_policy_docs_category_fkey'
    ) THEN
        ALTER TABLE data_pipeline.source_policy_docs
            ADD CONSTRAINT source_policy_docs_category_fkey
            FOREIGN KEY (category)
            REFERENCES data_pipeline.policy_source_categories(category);
    END IF;

    -- Add source check if missing and current data is compatible.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'source'
    )
    AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema    = 'data_pipeline'
          AND table_name      = 'source_policy_docs'
          AND constraint_type = 'CHECK'
          AND constraint_name = 'source_policy_docs_source_check'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM data_pipeline.source_policy_docs
            WHERE source NOT IN ('SharePoint', 'Confluence', 'GitHub')
        ) THEN
            RAISE WARNING 'Skipping source_policy_docs_source_check: found unsupported source values';
        ELSE
            ALTER TABLE data_pipeline.source_policy_docs
                ADD CONSTRAINT source_policy_docs_source_check
                CHECK (source IN ('SharePoint', 'Confluence', 'GitHub'));
        END IF;
    END IF;

    -- Add updated_at column if missing.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'data_pipeline'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'updated_at'
    ) THEN
        ALTER TABLE data_pipeline.source_policy_docs
            ADD COLUMN updated_at TIMESTAMPTZ NULL;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Output: one row per unique policy URL processed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.policy_documents (
    policy_doc_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url    TEXT        NOT NULL UNIQUE,
    filename      TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Output: extracted evaluation questions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.questions (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text  TEXT        NOT NULL,
    reference      TEXT        NOT NULL,
    source_excerpt TEXT        NOT NULL,
    policy_doc_id  UUID        NOT NULL
        REFERENCES data_pipeline.policy_documents(policy_doc_id) ON DELETE CASCADE,
    isactive       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Housekeeping: last-modified timestamp for change detection
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.policy_document_sync (
    url_hash       CHAR(64)    PRIMARY KEY,
    source_url     TEXT        NOT NULL,
    last_modified  TIMESTAMPTZ,
    content_size   INTEGER,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    policy_doc_id  UUID
        REFERENCES data_pipeline.policy_documents(policy_doc_id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Cost tracking: LLM token usage and estimated cost per policy document run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.policydoc_costusage (
    id             SERIAL      PRIMARY KEY,
    policy_doc_id  UUID        NOT NULL
        REFERENCES data_pipeline.policy_documents(policy_doc_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    input_tokens   INTEGER     NOT NULL,
    output_tokens  INTEGER     NOT NULL,
    amount         NUMERIC(10,4) NOT NULL,
    currency       VARCHAR(100)  NOT NULL DEFAULT 'USD',
    created_at     TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Seed: policy source URLs from JSON file (no hardcoded rows)
-- Expects /docker-entrypoint-initdb.d/policy_sources.json to be mounted.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    seed_json TEXT;
BEGIN
    BEGIN
        seed_json := pg_read_file('/docker-entrypoint-initdb.d/policy_sources.json');
    EXCEPTION
        WHEN OTHERS THEN
            RAISE WARNING 'Policy sources seed file not found/readable at /docker-entrypoint-initdb.d/policy_sources.json: %', SQLERRM;
            seed_json := '[]';
    END;

    -- Keep category reference table in sync with JSON source list.
    INSERT INTO data_pipeline.policy_source_categories (category)
    SELECT DISTINCT row.category
    FROM jsonb_to_recordset(seed_json::jsonb) AS row(
        url_id INTEGER,
        url TEXT,
        filename TEXT,
        category TEXT,
        source TEXT,
        isactive BOOLEAN
    )
    WHERE row.category IS NOT NULL
    ON CONFLICT (category) DO NOTHING;

    INSERT INTO data_pipeline.source_policy_docs (url, filename, category, source, isactive)
    SELECT
        row.url,
        row.filename,
        row.category,
        COALESCE(row.source, 'SharePoint') AS source,
        COALESCE(row.isactive, TRUE) AS isactive
    FROM jsonb_to_recordset(seed_json::jsonb) AS row(
        url_id INTEGER,
        url TEXT,
        filename TEXT,
        category TEXT,
        source TEXT,
        isactive BOOLEAN
    )
    ON CONFLICT (url) DO NOTHING;
END $$;
