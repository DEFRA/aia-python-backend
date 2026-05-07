-- Data Pipeline schema and tables
-- Applied automatically when the Podman postgres container first starts.

CREATE SCHEMA IF NOT EXISTS aia_app;

-- ---------------------------------------------------------------------------
-- Migration: rename legacy table if it exists under the old name
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'aia_app' AND table_name = 'source_path_policydoc'
    ) THEN
        ALTER TABLE aia_app.source_path_policydoc RENAME TO source_policy_docs;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: source_policy_docs — rename desp → filename, drop datasize
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'aia_app'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'desp'
    ) THEN
        ALTER TABLE aia_app.source_policy_docs RENAME COLUMN desp TO filename;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'aia_app'
          AND table_name   = 'source_policy_docs'
          AND column_name  = 'datasize'
    ) THEN
        ALTER TABLE aia_app.source_policy_docs DROP COLUMN datasize;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: policy_document_sync — drop file_name, add content_size
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'aia_app'
          AND table_name   = 'policy_document_sync'
          AND column_name  = 'file_name'
    ) THEN
        ALTER TABLE aia_app.policy_document_sync DROP COLUMN file_name;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'aia_app'
          AND table_name   = 'policy_document_sync'
          AND column_name  = 'content_size'
    ) THEN
        ALTER TABLE aia_app.policy_document_sync ADD COLUMN content_size INTEGER;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Migration: move category from question_categories to policy_documents
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    -- Add category column to policy_documents if not already present
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'aia_app'
          AND table_name   = 'policy_documents'
          AND column_name  = 'category'
    ) THEN
        ALTER TABLE aia_app.policy_documents ADD COLUMN category TEXT;
        -- Back-fill from question_categories via the most common category per document
        UPDATE aia_app.policy_documents pd
        SET    category = (
            SELECT qc.category
            FROM   aia_app.question_categories qc
            JOIN   aia_app.questions q ON q.question_id = qc.question_id
            WHERE  q.policy_doc_id = pd.policy_doc_id
            GROUP  BY qc.category
            ORDER  BY COUNT(*) DESC
            LIMIT  1
        );
        ALTER TABLE aia_app.policy_documents ALTER COLUMN category SET NOT NULL;
    END IF;
    -- Drop the junction table once data is migrated
    DROP TABLE IF EXISTS aia_app.question_categories;
END $$;

-- ---------------------------------------------------------------------------
-- Source: active policy URLs to process
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aia_app.source_policy_docs (
    url_id   SERIAL PRIMARY KEY,
    url      TEXT    NOT NULL UNIQUE,
    filename TEXT    NOT NULL,
    category TEXT    NOT NULL,
    type     TEXT    NOT NULL DEFAULT 'page',
    isactive BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- Output: one row per unique policy URL processed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aia_app.policy_documents (
    policy_doc_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url    TEXT        NOT NULL UNIQUE,
    filename      TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Output: extracted evaluation questions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aia_app.questions (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text  TEXT        NOT NULL,
    reference      TEXT        NOT NULL,
    source_excerpt TEXT        NOT NULL,
    policy_doc_id  UUID        NOT NULL
        REFERENCES aia_app.policy_documents(policy_doc_id) ON DELETE CASCADE,
    isactive       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Housekeeping: last-modified timestamp for change detection
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aia_app.policy_document_sync (
    url_hash       CHAR(64)    PRIMARY KEY,
    source_url     TEXT        NOT NULL,
    last_modified  TIMESTAMPTZ,
    content_size   INTEGER,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    policy_doc_id  UUID
        REFERENCES aia_app.policy_documents(policy_doc_id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Seed: known policy source URLs
-- Remove any row you do not want processed (or set isactive = FALSE).
-- ---------------------------------------------------------------------------
INSERT INTO aia_app.source_policy_docs (url, filename, category, type, isactive) VALUES
    (
        'https://defra.sharepoint.com/teams/Team3221/SitePages/Strategic-Architecture-Principles.aspx',
        'Strategic Architecture Principles',
        'technical', 'page', TRUE
    ),
    (
        'https://defra.sharepoint.com/teams/Team3221/Published%20Architecture%20Documentation/Forms/AllItems.aspx',
        'Defra Architecture - Published Guardrails - All Documents',
        'technical', 'page', TRUE
    ),
    (
        'https://defra.sharepoint.com/:b:/r/teams/Team3182/Tech%20Gov%20Docs/Tools%20Authority/Tools%20Radar/20260217%20DDTS_Tools_Authority_-%C2%A0_Supplier_Radar.pdf',
        'DDTS Tools Authority Supplier Radar',
        'technical', 'pdf', FALSE
    ),
    (
        'https://defra.sharepoint.com/sites/def-ddts-portfoliohub/SitePages/Secure-by-Design.aspx',
        'Secure by Design',
        'security', 'page', TRUE
    ),
    (
        'https://defra.sharepoint.com/teams/Team3221/Soln%20and%20App%20Architecture/Forms/AllItems.aspx',
        'Defra Architecture - Delivery Architecture Team - Solution Design Authority',
        'technical', 'page', FALSE
    ),
    (
        'https://defra.sharepoint.com/sites/Community3868/SitePages/Integration.aspx',
        'GIO Integration',
        'technical', 'page', TRUE
    ),
    (
        'https://defra.sharepoint.com/sites/Community448/SitePages/CDAP-The-Common-Data-Analytics-Platform.aspx',
        'The Data Analytics and Science Hub (DASH) Platform',
        'technical', 'page', TRUE
    ),
    (
        'https://defra.sharepoint.com/sites/Community3868/SitePages/Reporting.aspx',
        'GIO Data Platform',
        'technical', 'page', TRUE
    )
ON CONFLICT (url) DO NOTHING;
