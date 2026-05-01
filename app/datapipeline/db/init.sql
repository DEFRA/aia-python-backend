-- Data Pipeline schema and tables
-- Applied automatically when the Podman postgres container first starts.

CREATE SCHEMA IF NOT EXISTS data_pipeline;

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
-- Migration: move category from question_categories to policy_documents
-- ---------------------------------------------------------------------------
DO $$ BEGIN
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
    -- Drop the junction table once data is migrated
    DROP TABLE IF EXISTS data_pipeline.question_categories;
END $$;

-- ---------------------------------------------------------------------------
-- Source: active policy URLs to process
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.source_policy_docs (
    url_id   SERIAL PRIMARY KEY,
    url      TEXT    NOT NULL UNIQUE,
    desp     TEXT    NOT NULL,
    category TEXT    NOT NULL,
    type     TEXT    NOT NULL DEFAULT 'page',
    isactive BOOLEAN NOT NULL DEFAULT TRUE,
    datasize INTEGER
);

-- ---------------------------------------------------------------------------
-- Output: one row per unique policy URL processed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.policy_documents (
    policy_doc_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url    TEXT        NOT NULL UNIQUE,
    file_name     TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Output: extracted evaluation questions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_pipeline.questions (
    question_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
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
    file_name      TEXT        NOT NULL,
    last_modified  TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    policy_doc_id  UUID
        REFERENCES data_pipeline.policy_documents(policy_doc_id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Seed: known policy source URLs
-- Remove any row you do not want processed (or set isactive = FALSE).
-- ---------------------------------------------------------------------------
INSERT INTO data_pipeline.source_policy_docs (url, desp, category, type, isactive) VALUES
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
