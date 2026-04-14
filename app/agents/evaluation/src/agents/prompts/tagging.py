"""Tagging agent prompts and taxonomy."""

TAXONOMY: dict[str, str] = {
    "authentication": "Identity verification, MFA, passwords, session tokens, SSO",
    "authorisation": "Access control, permissions, RBAC, privilege, provisioning",
    "encryption": "TLS, AES, key management, data at rest/in transit",
    "vulnerability_management": "CVE, SAST, DAST, patching, dependency scanning",
    "audit_logging": "SIEM, event logging, audit trails, monitoring",
    "data_governance": "Data classification, retention, ownership, lineage, compliance",
    "incident_response": "Breach, remediation, SLA, escalation, forensics",
    "secrets_management": "API keys, credentials, vaults, rotation",
    "network_security": "Firewall, VPN, TLS, segmentation, ingress/egress",
    "compliance": "GDPR, ISO27001, SOC2, regulatory, policy",
}

SYSTEM_PROMPT: str = """You are a document security analyst. You will receive a JSON array
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
