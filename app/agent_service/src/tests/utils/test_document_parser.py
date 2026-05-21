"""Tests for utils.doc_parser — PDF and DOCX parsing functions."""

from __future__ import annotations

import io

import fitz
from docx import Document

from app.agent_service.src.utils.doc_parser import (
    clean_and_chunk,
    extract_text_blocks,
    get_pdf_strategy,
    parse_docx,
)

# ---------------------------------------------------------------------------
# Helpers — build minimal in-memory PDF and DOCX fixtures
# ---------------------------------------------------------------------------


def _make_text_pdf(pages: list[str]) -> bytes:
    """Create a minimal PDF with extractable text on each page."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    buf: bytes = doc.tobytes()
    doc.close()
    return buf


def _make_image_pdf() -> bytes:
    """Create a minimal image-only PDF (no extractable text)."""
    doc = fitz.open()
    page = doc.new_page()
    # Insert a tiny 1x1 red pixel PNG
    import struct
    import zlib

    def _make_png() -> bytes:
        raw = b"\x00\xff\x00\x00"
        compressed = zlib.compress(raw)
        png = b"\x89PNG\r\n\x1a\n"
        for chunk_type, data in [
            (b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
            (b"IDAT", compressed),
            (b"IEND", b""),
        ]:
            chunk = chunk_type + data
            png += struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        return png

    page.insert_image(fitz.Rect(0, 0, 100, 100), stream=_make_png())
    buf: bytes = doc.tobytes()
    doc.close()
    return buf


def _make_docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """Create a minimal DOCX with specified (style, text) paragraphs."""
    doc = Document()
    for style, text in paragraphs:
        doc.add_paragraph(text, style=style)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# get_pdf_strategy
# ---------------------------------------------------------------------------


class TestGetPdfStrategy:
    """Tests for PDF strategy detection."""

    def test_text_pdf_returns_text_strategy(self) -> None:
        pdf_bytes: bytes = _make_text_pdf(["Hello world"])
        assert get_pdf_strategy(pdf_bytes) == "text"

    def test_image_pdf_returns_ocr_strategy(self) -> None:
        pdf_bytes: bytes = _make_image_pdf()
        assert get_pdf_strategy(pdf_bytes) == "ocr"


# ---------------------------------------------------------------------------
# extract_text_blocks
# ---------------------------------------------------------------------------


class TestExtractTextBlocks:
    """Tests for extract_text_blocks()."""

    def test_returns_blocks_with_required_keys(self) -> None:
        pdf_bytes: bytes = _make_text_pdf(["First page content"])
        blocks: list[dict] = extract_text_blocks(pdf_bytes)  # type: ignore[type-arg]
        assert len(blocks) > 0
        b = blocks[0]
        assert "page" in b
        assert "block_no" in b
        assert "bbox" in b
        assert "text" in b

    def test_multi_page_includes_all_pages(self) -> None:
        pdf_bytes: bytes = _make_text_pdf(["Page one", "Page two", "Page three"])
        blocks: list[dict] = extract_text_blocks(pdf_bytes)  # type: ignore[type-arg]
        pages: set[int] = {b["page"] for b in blocks}
        assert pages == {1, 2, 3}


# ---------------------------------------------------------------------------
# clean_and_chunk
# ---------------------------------------------------------------------------


class TestCleanAndChunk:
    """Tests for clean_and_chunk()."""

    def test_returns_chunks_with_required_keys(self) -> None:
        blocks: list[dict] = [  # type: ignore[type-arg]
            {
                "page": 1,
                "block_no": 0,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [12.0],
                "font_names": ["Helvetica"],
                "text": "Hello world.",
            }
        ]
        chunks: list[dict] = clean_and_chunk(blocks)  # type: ignore[type-arg]
        assert len(chunks) > 0
        c = chunks[0]
        assert "chunk_index" in c
        assert "page" in c
        assert "is_heading" in c
        assert "char_count" in c
        assert "text" in c

    def test_heading_detection_by_font_size(self) -> None:
        """Blocks with font size > body threshold should be tagged as headings."""
        blocks: list[dict] = [  # type: ignore[type-arg]
            {
                "page": 1,
                "block_no": 0,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [12.0],
                "font_names": ["Helvetica"],
                "text": "Preceding body text",
            },
            {
                "page": 1,
                "block_no": 1,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [20.0],
                "font_names": ["Helvetica-Bold"],
                "text": "Section Title",
            },
            {
                "page": 1,
                "block_no": 2,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [12.0],
                "font_names": ["Helvetica"],
                "text": "Following body text",
            },
        ]
        chunks: list[dict] = clean_and_chunk(blocks)  # type: ignore[type-arg]
        assert any(c["is_heading"] for c in chunks)

    def test_heading_splits_chunk(self) -> None:
        """A heading should start a new chunk boundary."""
        blocks: list[dict] = [  # type: ignore[type-arg]
            {
                "page": 1,
                "block_no": 0,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [12.0],
                "font_names": ["Helvetica"],
                "text": "Preceding body text",
            },
            {
                "page": 1,
                "block_no": 1,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [20.0],
                "font_names": ["Helvetica-Bold"],
                "text": "Section Title",
            },
        ]
        chunks: list[dict] = clean_and_chunk(blocks)  # type: ignore[type-arg]
        assert len(chunks) >= 2
        assert "Preceding body text" in chunks[0]["text"]
        assert "Section Title" in chunks[1]["text"]

    def test_heading_only_block_is_heading(self) -> None:
        """A heading that is the last block in its chunk retains is_heading=True."""
        blocks: list[dict] = [  # type: ignore[type-arg]
            {
                "page": 1,
                "block_no": 0,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [12.0],
                "font_names": ["Helvetica"],
                "text": "Body text.",
            },
            {
                "page": 1,
                "block_no": 1,
                "bbox": [0, 0, 100, 100],
                "font_sizes": [20.0],
                "font_names": ["Helvetica-Bold"],
                "text": "Standalone Heading",
            },
        ]
        chunks: list[dict] = clean_and_chunk(blocks)  # type: ignore[type-arg]
        assert len(chunks) == 2
        assert chunks[1]["is_heading"] is True

    def test_empty_blocks_returns_empty(self) -> None:
        assert clean_and_chunk([]) == []


# ---------------------------------------------------------------------------
# parse_docx
# ---------------------------------------------------------------------------


class TestParseDocx:
    """Tests for parse_docx()."""

    def test_returns_chunk_schema(self) -> None:
        docx_bytes: bytes = _make_docx([("Normal", "Hello world.")])
        chunks: list[dict] = parse_docx(docx_bytes)  # type: ignore[type-arg]
        assert len(chunks) == 1
        c = chunks[0]
        assert "chunk_index" in c
        assert "page" in c
        assert "is_heading" in c
        assert "char_count" in c
        assert "text" in c

    def test_heading_style_detected(self) -> None:
        docx_bytes: bytes = _make_docx(
            [
                ("Heading 1", "My Title"),
                ("Normal", "Body paragraph."),
            ]
        )
        chunks: list[dict] = parse_docx(docx_bytes)  # type: ignore[type-arg]
        assert chunks[0]["is_heading"] is True
        assert chunks[1]["is_heading"] is False

    def test_empty_paragraphs_skipped(self) -> None:
        docx_bytes: bytes = _make_docx(
            [
                ("Normal", "Text"),
                ("Normal", ""),
                ("Normal", "More text"),
            ]
        )
        chunks: list[dict] = parse_docx(docx_bytes)  # type: ignore[type-arg]
        texts: list[str] = [c["text"] for c in chunks]
        assert "" not in texts

    def test_chunk_index_sequential(self) -> None:
        docx_bytes: bytes = _make_docx(
            [
                ("Normal", "First"),
                ("Normal", "Second"),
                ("Normal", "Third"),
            ]
        )
        chunks: list[dict] = parse_docx(docx_bytes)  # type: ignore[type-arg]
        indices: list[int] = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(indices)))
