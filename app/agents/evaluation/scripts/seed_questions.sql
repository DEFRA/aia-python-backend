-- Seed script for local development / end-to-end runner testing.
-- Creates one policy document per category and inserts sample questions.
-- Run against the aia_documents DB after applying app/datapipeline/db/init.sql.
--
-- Usage:
--   psql -U aiauser -d aia_documents -f seed_questions.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- Policy document placeholders
-- ---------------------------------------------------------------------------
INSERT INTO data_pipeline.policy_documents (policy_doc_id, source_url, file_name)
VALUES
    ('00000000-0000-0000-0000-000000000001',
     'https://example.com/security-policy', 'security-policy.md'),
    ('00000000-0000-0000-0000-000000000002',
     'https://example.com/technical-policy', 'technical-policy.md')
ON CONFLICT (source_url) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Security questions  (category = 'security')
-- ---------------------------------------------------------------------------
WITH sq (ref, text) AS (
    VALUES
    ('SEC-1',  'Does the document clearly state its purpose and scope, covering employees, contractors, temporary staff, and third-party partners?'),
    ('SEC-2',  'Does it enforce unique user accounts, prohibit credential sharing, and define least privilege/RBAC?'),
    ('SEC-3',  'Does it require Multi-Factor Authentication (MFA) for all critical systems?'),
    ('SEC-4',  'Does it require quarterly access reviews and removal of unused access?'),
    ('SEC-5',  'Does it require 12+ character passwords with complexity, prohibit sharing, and only require changes upon suspected compromise?'),
    ('SEC-6',  'Does it require automatic updates, approved AV/EDR, 5-minute screen lock, and device encryption?'),
    ('SEC-7',  'Does it require VPN for remote/public Wi-Fi access and prohibit unauthorized network devices?'),
    ('SEC-8',  'Does it define data classification, data minimization, encryption in transit & at rest, and approved file-sharing tools?'),
    ('SEC-9',  'Does it require sender verification, avoidance of unknown links/attachments, and immediate phishing reporting?'),
    ('SEC-10', 'Does it require ID badges, workstation locking, 1-hour lost/stolen device reporting, and immediate incident escalation?')
),
inserted AS (
    INSERT INTO data_pipeline.questions (question_text, reference, source_excerpt, policy_doc_id)
    SELECT text, ref, '', '00000000-0000-0000-0000-000000000001'
    FROM sq
    ON CONFLICT DO NOTHING
    RETURNING question_id, reference
)
INSERT INTO data_pipeline.question_categories (question_id, category)
SELECT question_id, 'security' FROM inserted
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Technical / information-governance questions  (category = 'technical')
-- ---------------------------------------------------------------------------
WITH tq (ref, text) AS (
    VALUES
    ('TEC-1',  'Does the document identify the data controller and processor roles in line with UK GDPR Article 4?'),
    ('TEC-2',  'Is there a Record of Processing Activities (ROPA) maintained per UK GDPR Article 30?'),
    ('TEC-3',  'Are data retention schedules and disposal procedures documented?'),
    ('TEC-4',  'Does the document describe how Data Subject Access Requests (DSARs) are handled within statutory timeframes?'),
    ('TEC-5',  'Is the lawful basis for each processing activity identified under UK GDPR Article 6 (or Article 9 for special-category data)?'),
    ('TEC-6',  'Are Articles 13/14 transparency disclosures (privacy notices) in place for data subjects?'),
    ('TEC-7',  'Has a Data Protection Impact Assessment (DPIA) been completed where high-risk processing is involved?'),
    ('TEC-8',  'Are data-sharing agreements and international transfer safeguards documented?'),
    ('TEC-9',  'Are DPO, IAO, and SIRO roles identified and accountable within the governance framework?'),
    ('TEC-10', 'Is an audit trail maintained to demonstrate compliance with the accountability principle (Article 5(2) UK GDPR)?')
),
inserted AS (
    INSERT INTO data_pipeline.questions (question_text, reference, source_excerpt, policy_doc_id)
    SELECT text, ref, '', '00000000-0000-0000-0000-000000000002'
    FROM tq
    ON CONFLICT DO NOTHING
    RETURNING question_id, reference
)
INSERT INTO data_pipeline.question_categories (question_id, category)
SELECT question_id, 'technical' FROM inserted
ON CONFLICT DO NOTHING;

COMMIT;

-- Verify
SELECT qc.category, COUNT(*) AS questions
FROM data_pipeline.question_categories qc
GROUP BY qc.category
ORDER BY qc.category;
