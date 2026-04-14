# PDF Security Tagging Pipeline

A progressive guide to building an LLM-powered pipeline that reads a PDF, extracts its text, and tags each section with security and governance labels — outputting a structured markdown file.

---

## Overview

The pipeline has three distinct stages, each with a clear responsibility:

```
PDF
 │
 ▼
extract_text_blocks()        ← pymupdf, deterministic, free
 │  [page, block_no, bbox, font_sizes, text]
 ▼
clean_and_chunk()            ← your code, free
 │  [chunk_index, page, is_heading, char_count, text]
 ▼
tag_chunks()                 ← Claude, expensive — runs once, cache result
 │  [+ relevant, tags, reason]
 ▼
render_tagged_markdown()     ← your code, free
    [.tagged.md]
```

The core principle is **separation of concerns by token cost and determinism**. `pymupdf` does what it's uniquely good at (byte-level text extraction), and the LLM does what it's uniquely good at (semantic judgment). Never pay LLM token rates for work a library does in microseconds.

---

## Stage 1 — Extract Text from the PDF

The extraction stage pulls raw text blocks from the PDF with spatial metadata. It uses `fitz.get_text("dict")` rather than the simpler `"blocks"` mode because it exposes font size per span — which is used later for heading detection.

```python
"""PDF text extraction stage — output is the agent's input."""

import json
from pathlib import Path

import fitz  # pymupdf


def extract_text_blocks(pdf_path: Path) -> list[dict]:
    """Extract raw text blocks from PDF with spatial metadata.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        List of block dicts with keys: page, block_no, text, bbox, font_sizes.
    """
    doc = fitz.open(pdf_path)
    blocks = []

    for page_num, page in enumerate(doc, start=1):
        raw_blocks = page.get_text("dict")["blocks"]  # richer than "blocks" mode

        for block in raw_blocks:
            if block["type"] != 0:  # 0 = text, 1 = image
                continue

            spans = [
                span
                for line in block["lines"]
                for span in line["spans"]
            ]

            if not spans:
                continue

            text = " ".join(s["text"].strip() for s in spans if s["text"].strip())
            if not text:
                continue

            font_sizes = list({round(s["size"], 1) for s in spans})
            font_names = list({s["font"] for s in spans})

            blocks.append({
                "page": page_num,
                "block_no": block["number"],
                "bbox": [round(v, 1) for v in block["bbox"]],  # x0,y0,x1,y1
                "font_sizes": font_sizes,
                "font_names": font_names,
                "text": text,
            })

    doc.close()
    return blocks
```

---

## Stage 2 — Clean and Chunk

Raw blocks are too granular to send to the LLM efficiently — a single paragraph may be split across several blocks. This stage merges them into chunks under a character limit, and attaches a heading flag based on font size relative to the page's dominant body font.

```python
def clean_and_chunk(
    blocks: list[dict],
    max_chars: int = 1500,
) -> list[dict]:
    """Merge small blocks into chunks and attach heading hints.

    Heading detection is heuristic: the largest font size on a page that
    exceeds the body font by 10% is treated as a heading. This gives the
    agent structural context without a full layout parser.

    Args:
        blocks: Output from extract_text_blocks().
        max_chars: Soft max characters per chunk before forcing a split.

    Returns:
        List of chunk dicts ready to pass to the tagging agent.
    """
    from collections import Counter

    page_font_counter: dict[int, Counter] = {}
    for b in blocks:
        page = b["page"]
        if page not in page_font_counter:
            page_font_counter[page] = Counter()
        for fs in b["font_sizes"]:
            page_font_counter[page][fs] += 1

    body_font: dict[int, float] = {
        page: counter.most_common(1)[0][0]
        for page, counter in page_font_counter.items()
    }

    chunks: list[dict] = []
    idx = 0
    current_text = ""
    current_page = blocks[0]["page"] if blocks else 1
    current_is_heading = False

    def flush(text: str, page: int, is_heading: bool) -> dict:
        return {
            "chunk_index": idx,
            "page": page,
            "is_heading": is_heading,
            "char_count": len(text),
            "text": text.strip(),
        }

    for block in blocks:
        page = block["page"]
        text = block["text"]
        max_font = max(block["font_sizes"]) if block["font_sizes"] else 0
        is_heading = max_font > body_font.get(page, 0) * 1.1

        force_flush = is_heading or (len(current_text) + len(text) > max_chars)

        if force_flush and current_text.strip():
            chunks.append(flush(current_text, current_page, current_is_heading))
            idx += 1
            current_text = ""

        current_text += (" " if current_text else "") + text
        current_page = page
        current_is_heading = is_heading

    if current_text.strip():
        chunks.append(flush(current_text, current_page, current_is_heading))

    return chunks
```

---

## What a Multi-Page Parsed PDF Looks Like

Before tagging, it's worth seeing exactly what the agent receives. Here is the output from a 4-page security policy document — this is the verbatim JSON passed to the LLM.

```json
[
  {
    "chunk_index": 0,
    "page": 1,
    "is_heading": true,
    "char_count": 52,
    "text": "Information Security Policy v2.4"
  },
  {
    "chunk_index": 1,
    "page": 1,
    "is_heading": false,
    "char_count": 298,
    "text": "This document defines the security requirements for all systems operated by Acme Corp. It applies to all employees, contractors, and third-party vendors with access to company data or infrastructure. Compliance is mandatory and violations may result in disciplinary action."
  },
  {
    "chunk_index": 2,
    "page": 2,
    "is_heading": true,
    "char_count": 38,
    "text": "3. Access Control and Authentication"
  },
  {
    "chunk_index": 3,
    "page": 2,
    "is_heading": false,
    "char_count": 401,
    "text": "All user accounts must be protected with multi-factor authentication (MFA). Passwords must be at least 16 characters and rotated every 90 days. Privileged accounts (admin, root) require hardware security keys. Access to production systems is restricted to named individuals and must be approved by the system owner prior to provisioning."
  },
  {
    "chunk_index": 4,
    "page": 2,
    "is_heading": false,
    "char_count": 187,
    "text": "Session tokens expire after 15 minutes of inactivity. All authentication events must be logged to the SIEM with user ID, timestamp, source IP, and success/failure status."
  },
  {
    "chunk_index": 5,
    "page": 3,
    "is_heading": true,
    "char_count": 29,
    "text": "4. Software Development Lifecycle"
  },
  {
    "chunk_index": 6,
    "page": 3,
    "is_heading": false,
    "char_count": 245,
    "text": "All development teams follow a two-week sprint cadence. Code reviews are required before merging to main. The engineering team uses GitHub for version control and Jira for issue tracking. Releases are managed via a CI/CD pipeline."
  },
  {
    "chunk_index": 7,
    "page": 3,
    "is_heading": false,
    "char_count": 334,
    "text": "Static analysis security testing (SAST) must be run on every pull request. Dependencies are scanned for known CVEs using Dependabot. Secrets must never be committed to source control — use the secrets manager. Any critical vulnerability must be remediated within 48 hours of discovery."
  },
  {
    "chunk_index": 8,
    "page": 4,
    "is_heading": true,
    "char_count": 24,
    "text": "5. Data Encryption Standards"
  },
  {
    "chunk_index": 9,
    "page": 4,
    "is_heading": false,
    "char_count": 389,
    "text": "Data at rest must be encrypted using AES-256. Data in transit requires TLS 1.2 minimum; TLS 1.3 is preferred. Database backups must be encrypted before transfer to cold storage. Encryption keys are managed via AWS KMS with 90-day automatic rotation. Key access is audited and restricted to the platform security team."
  },
  {
    "chunk_index": 10,
    "page": 4,
    "is_heading": false,
    "char_count": 91,
    "text": "Document owner: CISO. Review cycle: annual. Next review due: 2025-03-01."
  }
]
```

Three things worth noting about this structure:

- **Page boundaries don't interrupt chunks.** `chunk_index` is a flat sequence across the whole document. The agent tags chunks; `page` is pass-through metadata for the output.
- **Chunk 6 vs Chunk 7 on page 3.** Two chunks on the same page — one non-security (sprint cadence, Jira) and one security-relevant (SAST, CVE scanning, secrets). Chunking by `max_chars` naturally separates them so the agent can tag selectively rather than flagging the entire page.
- **Headings prime the agent semantically.** Chunk 2 (`is_heading=true`, "Access Control and Authentication") immediately precedes chunks 3 and 4. When batched together, the heading gives the agent section context before it reads the body text.

Fields deliberately excluded from the agent payload:

| Extracted but dropped | Why |
|---|---|
| `bbox` coordinates | Spatial layout is irrelevant to semantic tagging |
| `font_names` | Font family doesn't inform security relevance |
| `font_sizes` | Already consumed to produce `is_heading` |
| `block_no` | Internal pymupdf artefact, not meaningful to the LLM |

---

## Stage 3 — Tag Chunks with the LLM

The agent receives the chunk JSON and returns it enriched with `relevant`, `tags`, and `reason`. The taxonomy is injected into the system prompt so the LLM works from a fixed, controlled label set.

```python
"""Tagging agent — receives extracted chunks, returns tagged chunks."""

import json
import re
from pathlib import Path

import anthropic


TAXONOMY = {
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


SYSTEM_PROMPT = """You are a document security analyst. You will receive a JSON array
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
""".format(
    taxonomy="\n".join(f"  {k}: {v}" for k, v in TAXONOMY.items())
)


def tag_chunks(
    chunks: list[dict],
    client: anthropic.Anthropic,
    batch_size: int = 15,
) -> list[dict]:
    """Send extracted chunks to Claude for security/governance tagging.

    Args:
        chunks: Output from clean_and_chunk() — chunk_index, page,
                is_heading, char_count, text.
        client: Anthropic client instance.
        batch_size: Chunks per API call. Keep low enough that prompt +
                    chunks + response fits comfortably in context.

    Returns:
        Tagged chunks preserving all original fields plus: relevant, tags, reason.
    """
    tagged: list[dict] = []

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(batch, ensure_ascii=False, indent=2),
                }
            ],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        batch_tagged = json.loads(raw)
        tagged.extend(batch_tagged)
        print(f"  Tagged chunks {batch_start}–{batch_start + len(batch) - 1}")

    return tagged
```

---

## What the Agent Returns

The agent returns the same array, with three fields added to each chunk.

**Non-relevant chunk** — development process content, no security overlap:

```json
{
  "chunk_index": 6,
  "page": 3,
  "is_heading": false,
  "text": "All development teams follow a two-week sprint cadence...",
  "relevant": false,
  "tags": [],
  "reason": null
}
```

**Single-tag chunk** — section heading for encryption:

```json
{
  "chunk_index": 8,
  "page": 4,
  "is_heading": true,
  "text": "5. Data Encryption Standards",
  "relevant": true,
  "tags": ["encryption"],
  "reason": "Section heading for encryption standards — context for following chunks."
}
```

**Multi-tag chunk** — SAST, CVE scanning, and secrets hygiene in one paragraph:

```json
{
  "chunk_index": 7,
  "page": 3,
  "is_heading": false,
  "text": "Static analysis security testing (SAST) must be run on every pull request...",
  "relevant": true,
  "tags": ["vulnerability_management", "secrets_management", "compliance"],
  "reason": "Mandates SAST, CVE scanning, and secrets hygiene with a 48-hour remediation SLA."
}
```

The multi-tag case is the primary value-add over a keyword search approach — the LLM recognises that a single paragraph simultaneously touches vulnerability management (SAST, CVEs), secrets management (no secrets in source control), and compliance (remediation SLA), and tags it accordingly.

---

## Caching the Extraction Output

The tagged JSON is the natural checkpoint in the pipeline. Cache the extraction output so you can re-run tagging with different prompts or an expanded taxonomy without re-parsing the PDF.

```python
def prepare_agent_input(pdf_path: Path) -> list[dict]:
    """Full extraction pipeline with caching.

    Args:
        pdf_path: Path to the PDF.

    Returns:
        List of chunk dicts ready to pass to the tagging agent.
    """
    cache_path = pdf_path.with_suffix(".chunks.json")

    if cache_path.exists():
        print(f"Loading cached chunks from {cache_path}")
        return json.loads(cache_path.read_text())

    blocks = extract_text_blocks(pdf_path)
    chunks = clean_and_chunk(blocks)
    cache_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False))
    print(f"Extracted {len(chunks)} chunks → cached to {cache_path}")
    return chunks
```

---

## Limitations

- **Multi-column layouts** — `get_text("dict")` block order gets scrambled for academic papers with two-column layouts. Use `pymupdf4llm` or `pdfplumber` for those.
- **Scanned PDFs** — no text layer means no extraction. Detect this upfront and fall back to sending page images to a vision model:

```python
def get_pdf_strategy(pdf_path: Path) -> str:
    """Return 'text' if PDF has extractable text layer, else 'vision'."""
    doc = fitz.open(pdf_path)
    sample = "".join(doc[i].get_text() for i in range(min(3, len(doc))))
    doc.close()
    return "text" if len(sample.strip()) > 100 else "vision"
```

- **Tables with security data** — blocks fragment table cells. If your documents contain structured security controls in tabular form, add a dedicated table extraction pass before chunking.
