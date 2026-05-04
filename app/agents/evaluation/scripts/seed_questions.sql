-- Seed script for local development / E2E testing.
-- Creates one policy document per category and inserts sample questions.
-- Requires data_pipeline schema to exist (apply app/datapipeline/db/init.sql first).
--
-- Usage (from repo root):
--   /opt/podman/bin/podman exec aiadocuments psql -U aiauser -d aiadocuments \
--     -f /path/to/app/agents/evaluation/scripts/seed_questions.sql
--
-- Safe to re-run: all INSERTs use ON CONFLICT DO NOTHING.

BEGIN;

-- ---------------------------------------------------------------------------
-- Policy document placeholders (one per category)
-- category must match LOWER(category) lookup in questions_repo.py
-- ---------------------------------------------------------------------------
INSERT INTO data_pipeline.policy_documents (policy_doc_id, source_url, filename, category)
VALUES
    ('00000000-0000-0000-0000-000000000001',
     'https://example.com/security-policy',
     'security-policy.md',
     'security'),
    ('00000000-0000-0000-0000-000000000002',
     'https://example.com/technical-policy',
     'technical-policy.md',
     'technical')
ON CONFLICT (source_url) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Security questions
-- ---------------------------------------------------------------------------
INSERT INTO data_pipeline.questions (question_text, reference, source_excerpt, policy_doc_id)
VALUES
    ('Does the document clearly state its purpose and scope, covering employees, contractors, temporary staff, and third-party partners?',
     'SEC-1', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it enforce unique user accounts, prohibit credential sharing, and define least privilege/RBAC?',
     'SEC-2', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require Multi-Factor Authentication (MFA) for all critical systems?',
     'SEC-3', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require quarterly access reviews and removal of unused access?',
     'SEC-4', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require 12+ character passwords with complexity, prohibit sharing, and only require changes upon suspected compromise?',
     'SEC-5', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require automatic updates, approved AV/EDR, 5-minute screen lock, and device encryption?',
     'SEC-6', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require VPN for remote/public Wi-Fi access and prohibit unauthorized network devices?',
     'SEC-7', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it define data classification, data minimization, encryption in transit & at rest, and approved file-sharing tools?',
     'SEC-8', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require sender verification, avoidance of unknown links/attachments, and immediate phishing reporting?',
     'SEC-9', '', '00000000-0000-0000-0000-000000000001'),
    ('Does it require ID badges, workstation locking, 1-hour lost/stolen device reporting, and immediate incident escalation?',
     'SEC-10', '', '00000000-0000-0000-0000-000000000001')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Technical / information-governance questions
-- ---------------------------------------------------------------------------
INSERT INTO data_pipeline.questions (question_text, reference, source_excerpt, policy_doc_id)
VALUES
    ('Does the document identify the data controller and processor roles in line with UK GDPR Article 4?',
     'TEC-1', '', '00000000-0000-0000-0000-000000000002'),
    ('Is there a Record of Processing Activities (ROPA) maintained per UK GDPR Article 30?',
     'TEC-2', '', '00000000-0000-0000-0000-000000000002'),
    ('Are data retention schedules and disposal procedures documented?',
     'TEC-3', '', '00000000-0000-0000-0000-000000000002'),
    ('Does the document describe how Data Subject Access Requests (DSARs) are handled within statutory timeframes?',
     'TEC-4', '', '00000000-0000-0000-0000-000000000002'),
    ('Is the lawful basis for each processing activity identified under UK GDPR Article 6 (or Article 9 for special-category data)?',
     'TEC-5', '', '00000000-0000-0000-0000-000000000002'),
    ('Are Articles 13/14 transparency disclosures (privacy notices) in place for data subjects?',
     'TEC-6', '', '00000000-0000-0000-0000-000000000002'),
    ('Has a Data Protection Impact Assessment (DPIA) been completed where high-risk processing is involved?',
     'TEC-7', '', '00000000-0000-0000-0000-000000000002'),
    ('Are data-sharing agreements and international transfer safeguards documented?',
     'TEC-8', '', '00000000-0000-0000-0000-000000000002'),
    ('Are DPO, IAO, and SIRO roles identified and accountable within the governance framework?',
     'TEC-9', '', '00000000-0000-0000-0000-000000000002'),
    ('Is an audit trail maintained to demonstrate compliance with the accountability principle (Article 5(2) UK GDPR)?',
     'TEC-10', '', '00000000-0000-0000-0000-000000000002')
ON CONFLICT DO NOTHING;

COMMIT;

-- Verify
SELECT pd.category, COUNT(q.id) AS questions
FROM data_pipeline.policy_documents pd
LEFT JOIN data_pipeline.questions q ON q.policy_doc_id = pd.policy_doc_id
GROUP BY pd.category
ORDER BY pd.category;
