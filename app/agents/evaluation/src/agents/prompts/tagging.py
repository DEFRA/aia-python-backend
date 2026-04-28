"""Tagging agent prompts and taxonomy.

Taxonomy is partitioned by downstream agent:
- Security tags map to the SecurityAgent's checklist categories.
- Governance tags map to the GovernanceAgent's UK information-governance
  remit (DPA 2018 / UK GDPR / public-sector records management).

Adding or removing a tag here must stay in lock-step with
``pipeline.agent_tag_map`` in ``app/agents/evaluation/config.yaml``;
otherwise tagged chunks will silently fail to fan out to an agent.
"""

TAXONOMY: dict[str, str] = {
    # --- Security ---
    "authentication": "Identity verification, MFA, passwords, session tokens, SSO",
    "authorisation": "Access control, permissions, RBAC, privilege, provisioning",
    "encryption": "TLS, AES, key management, data at rest/in transit",
    "vulnerability_management": "CVE, SAST, DAST, patching, dependency scanning",
    "secrets_management": "API keys, credentials, vaults, rotation",
    "network_security": "Firewall, VPN, TLS, segmentation, ingress/egress",
    # --- Information Governance (UK GDPR / DPA 2018 / records management) ---
    "data_protection": "DPA 2018 / UK GDPR scope, controller/processor roles, accountability",
    "records_of_processing": "ROPA per UK GDPR Article 30, processing register, metadata",
    "data_retention": "Retention schedules, disposal procedures, archival, deletion",
    "data_subject_rights": "DSAR, erasure, rectification, portability, objection, restriction",
    "lawful_basis": "Article 6 lawful basis, Article 9 special-category conditions, consent",
    "privacy_notice": "Transparency information, Articles 13/14 disclosures, fair processing",
    "dpia": "Data Protection Impact Assessment, residual risk, prior consultation",
    "data_sharing": "Data sharing agreements, joint controllers, international transfers",
    "ig_governance": "DPO, IAO, SIRO roles, IG board, accountability framework",
    "audit_trail": "Access logging, evidential trail under Article 5(2) accountability",
    "information_classification": "OFFICIAL / OFFICIAL-SENSITIVE / SECRET / TOP SECRET handling",
}

SYSTEM_PROMPT: str = """You are a document security and information-governance analyst. You will receive a JSON array
of document chunks. For each chunk, determine which security or governance topics it covers.

Available tags and their meaning:
{taxonomy}

Rules:
- A chunk can have multiple tags if content genuinely overlaps
- is_heading=true chunks should inherit tags from their content, not just their title
- Non-relevant chunks get an empty tags list and relevant=false
- reason: one sentence max, only for relevant chunks, null otherwise

Return ONLY a valid JSON array. No markdown, no preamble. Each element must have exactly:
  chunk_index, page, is_heading, text, relevant, tags, reason
""".format(taxonomy="\n".join(f"  {k}: {v}" for k, v in TAXONOMY.items()))
