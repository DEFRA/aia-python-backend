"""Document parsing utilities for PDF and DOCX files.

Accepts raw ``bytes`` (Lambda downloads from S3 into memory) and produces
chunk dicts compatible with the tagging agent input schema.
"""

from __future__ import annotations

import io
import logging
from collections import Counter
from typing import Any

import fitz
from docx import Document

from src.config import ParserConfig

logger: logging.Logger = logging.getLogger(__name__)

_parser_config: ParserConfig | None = None


def _get_parser_config() -> ParserConfig:
    """Return the module-level ``ParserConfig`` singleton, creating on first call."""
    global _parser_config  # noqa: PLW0603
    if _parser_config is None:
        _parser_config = ParserConfig()
    return _parser_config


def get_pdf_strategy(file_bytes: bytes, min_text_chars: int | None = None) -> str:
    """Return ``"text"`` if the PDF has an extractable text layer, else ``"vision"``.

    Samples the first 3 pages.  If the combined stripped text is ``<=
    min_text_chars`` the PDF is treated as scanned / image-only.

    Args:
        file_bytes: Raw PDF bytes.
        min_text_chars: Optional override for the text-layer threshold.
            Defaults to ``ParserConfig.min_text_chars`` (configured in
            ``config.yaml``).
    """
    threshold: int = (
        min_text_chars if min_text_chars is not None else _get_parser_config().min_text_chars
    )
    doc: fitz.Document = fitz.open(stream=file_bytes, filetype="pdf")
    sample: str = "".join(doc[i].get_text() for i in range(min(3, len(doc))))
    doc.close()
    return "text" if len(sample.strip()) > threshold else "vision"


def extract_text_blocks(file_bytes: bytes) -> list[dict[str, Any]]:
    """Extract raw text blocks from a PDF with spatial metadata.

    Opens the PDF from *file_bytes* via ``fitz.open(stream=...)``.  For each
    text block on every page, captures page number, block number, bounding box,
    font sizes, font names, and the concatenated span text.

    Args:
        file_bytes: Raw PDF bytes.

    Returns:
        List of block dicts with keys: ``page``, ``block_no``, ``bbox``,
        ``font_sizes``, ``font_names``, ``text``.
    """
    doc: fitz.Document = fitz.open(stream=file_bytes, filetype="pdf")
    blocks: list[dict[str, Any]] = []

    for page_num, page in enumerate(doc, start=1):
        raw_blocks: list[dict[str, Any]] = page.get_text("dict")["blocks"]

        for block in raw_blocks:
            if block["type"] != 0:  # 0 = text, 1 = image
                continue

            spans: list[dict[str, Any]] = [
                span for line in block["lines"] for span in line["spans"]
            ]

            if not spans:
                continue

            text: str = " ".join(s["text"].strip() for s in spans if s["text"].strip())
            if not text:
                continue

            font_sizes: list[float] = list({round(s["size"], 1) for s in spans})
            font_names: list[str] = list({s["font"] for s in spans})

            blocks.append(
                {
                    "page": page_num,
                    "block_no": block["number"],
                    "bbox": [round(v, 1) for v in block["bbox"]],
                    "font_sizes": font_sizes,
                    "font_names": font_names,
                    "text": text,
                }
            )

    doc.close()
    return blocks


def clean_and_chunk(
    blocks: list[dict[str, Any]],
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Merge small blocks into chunks and attach heading hints.

    Heading detection is heuristic: the largest font size on a page that
    exceeds the body font by 10 % is treated as a heading.  This gives the
    agent structural context without a full layout parser.

    Args:
        blocks: Output from :func:`extract_text_blocks`.
        max_chars: Soft max characters per chunk before forcing a split.
            Defaults to ``ParserConfig.chunk_max_chars`` (configured in
            ``config.yaml``).

    Returns:
        List of chunk dicts with keys: ``chunk_index``, ``page``,
        ``is_heading``, ``char_count``, ``text``.
    """
    if not blocks:
        return []

    effective_max_chars: int = (
        max_chars if max_chars is not None else _get_parser_config().chunk_max_chars
    )

    # Build per-page body-font lookup
    page_font_counter: dict[int, Counter[float]] = {}
    for b in blocks:
        page: int = b["page"]
        if page not in page_font_counter:
            page_font_counter[page] = Counter()
        for fs in b["font_sizes"]:
            page_font_counter[page][fs] += 1

    body_font: dict[int, float] = {
        pg: counter.most_common(1)[0][0] for pg, counter in page_font_counter.items()
    }

    chunks: list[dict[str, Any]] = []
    idx: int = 0
    current_text: str = ""
    current_page: int = blocks[0]["page"] if blocks else 1
    current_is_heading: bool = False

    def flush(text: str, pg: int, is_heading: bool) -> dict[str, Any]:
        return {
            "chunk_index": idx,
            "page": pg,
            "is_heading": is_heading,
            "char_count": len(text),
            "text": text.strip(),
        }

    for block in blocks:
        pg = block["page"]
        text: str = block["text"]
        max_font: float = max(block["font_sizes"]) if block["font_sizes"] else 0
        is_heading: bool = max_font > body_font.get(pg, 0) * 1.1

        force_flush: bool = is_heading or (len(current_text) + len(text) > effective_max_chars)

        if force_flush and current_text.strip():
            chunks.append(flush(current_text, current_page, current_is_heading))
            idx += 1
            current_text = ""

        current_text += (" " if current_text else "") + text
        current_page = pg
        current_is_heading = is_heading

    if current_text.strip():
        chunks.append(flush(current_text, current_page, current_is_heading))

    return chunks


def parse_docx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a DOCX file to the same chunk schema as PDF parsing.

    Uses paragraph style names for heading detection (styles starting with
    ``"Heading"``).  Each non-empty paragraph becomes a chunk; the paragraph
    index serves as a proxy for page number.

    Args:
        file_bytes: Raw DOCX bytes.

    Returns:
        List of chunk dicts with keys: ``chunk_index``, ``page``,
        ``is_heading``, ``char_count``, ``text``.
    """
    doc = Document(io.BytesIO(file_bytes))
    chunks: list[dict[str, Any]] = []
    idx: int = 0

    for para_idx, para in enumerate(doc.paragraphs):
        text: str = para.text.strip()
        if not text:
            continue

        style_name: str = para.style.name if para.style else ""
        is_heading: bool = style_name.startswith("Heading")

        chunks.append(
            {
                "chunk_index": idx,
                "page": para_idx + 1,
                "is_heading": is_heading,
                "char_count": len(text),
                "text": text,
            }
        )
        idx += 1

    return chunks
