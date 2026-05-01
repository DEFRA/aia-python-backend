"""Tests for src/handlers/parse.py — Stage 3 Parse Lambda handler (Plan 11)."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fitz
import pytest

from src.utils.exceptions import ScannedPdfError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_pdf(text: str = "Security controls overview. " * 20) -> bytes:
    """Create a minimal PDF with extractable text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    buf: bytes = doc.tobytes()
    doc.close()
    return buf


def _make_sqs_event(doc_id: str = "doc-001", s3_key: str = "uploads/test.pdf") -> dict[str, Any]:
    """Build a minimal SQS event matching the handler's expected schema."""
    body: dict[str, str] = {"document_id": doc_id, "s3Key": s3_key}
    return {
        "Records": [
            {
                "receiptHandle": "test-receipt-handle-abc",
                "body": json.dumps(body),
            }
        ],
    }


def _make_docx_bytes() -> bytes:
    """Create a minimal DOCX."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Test paragraph content for parsing.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSqsEventValidation:
    """SQS event Pydantic validation."""

    def test_valid_event_parses(self) -> None:
        from src.handlers.parse import SqsEvent, SqsRecordBody

        event: dict[str, Any] = _make_sqs_event()
        parsed: SqsEvent = SqsEvent.model_validate(event)
        assert len(parsed.Records) == 1
        body: SqsRecordBody = SqsRecordBody.model_validate_json(parsed.Records[0].body)
        assert body.document_id == "doc-001"
        assert body.s3Key == "uploads/test.pdf"

    def test_missing_records_raises(self) -> None:
        from pydantic import ValidationError

        from src.handlers.parse import SqsEvent

        with pytest.raises(ValidationError):
            SqsEvent.model_validate({})

    def test_missing_body_fields_raises(self) -> None:
        from pydantic import ValidationError

        from src.handlers.parse import SqsRecordBody

        with pytest.raises(ValidationError):
            SqsRecordBody.model_validate({"document_id": "x"})  # missing s3Key


@pytest.mark.asyncio
class TestParseHandler:
    """Integration tests for the parse handler `_handler` function."""

    async def test_parse_handler_publishes_event_with_inline_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Small parsed output is carried inline in the DocumentParsed event."""
        pdf_bytes: bytes = _make_text_pdf()

        async def mock_download(s3_client: Any, bucket: str, key: str) -> bytes:
            return pdf_bytes

        published: list[dict[str, Any]] = []
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        s3_client = MagicMock()  # never called for inline path

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._get_s3", lambda: s3_client)
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/test.pdf")
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        assert len(published) == 1
        assert published[0]["detail_type"] == "DocumentParsed"
        detail: dict[str, Any] = published[0]["detail"]
        assert detail["document_id"] == "doc-001"
        # Payload envelope is inline (small parse)
        assert "payload" in detail
        assert "inline" in detail["payload"]
        assert "s3Key" not in detail["payload"]
        # No Redis fields any more
        assert "chunksCacheKey" not in detail
        assert "contentHash" not in detail
        # No S3 offload occurred
        s3_client.put_object.assert_not_called()

    async def test_parse_handler_offloads_large_chunks_to_s3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Large parsed output is offloaded to S3 via the s3Key envelope."""
        pdf_bytes: bytes = _make_text_pdf("Lorem ipsum dolor. ")

        # Force the offload path by patching the parser to emit a huge payload.
        big_chunks: list[dict[str, Any]] = [
            {"chunk_index": i, "page": 1, "text": "x" * 1000} for i in range(300)
        ]

        async def mock_download(s3_client: Any, bucket: str, key: str) -> bytes:
            return pdf_bytes

        published: list[dict[str, Any]] = []
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        s3_client = MagicMock()
        s3_client.put_object = MagicMock()

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._get_s3", lambda: s3_client)
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        # Bypass real PDF parsing — return the big synthetic chunks
        monkeypatch.setattr("src.handlers.parse.get_pdf_strategy", lambda b: "text")
        monkeypatch.setattr("src.handlers.parse.extract_text_blocks", lambda b: [])
        monkeypatch.setattr("src.handlers.parse.clean_and_chunk", lambda blocks: big_chunks)
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/big.pdf", doc_id="big-doc")
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        # Envelope used s3Key, not inline
        detail: dict[str, Any] = published[0]["detail"]
        assert "s3Key" in detail["payload"]
        assert detail["payload"]["s3Key"] == "state/big-doc/chunks.json"
        s3_client.put_object.assert_called_once()

    async def test_scanned_pdf_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A scanned PDF (no text layer) raises ScannedPdfError."""
        blank_doc = fitz.open()
        blank_doc.new_page()
        blank_bytes: bytes = blank_doc.tobytes()
        blank_doc.close()

        async def mock_download(s3_client: Any, bucket: str, key: str) -> bytes:
            return blank_bytes

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._get_s3", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/scanned.pdf")

        with pytest.raises(ScannedPdfError):
            await _handler(event, {})

    async def test_unsupported_extension_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-PDF/DOCX file extension raises ValueError."""

        async def mock_download(s3_client: Any, bucket: str, key: str) -> bytes:
            return b"not a real file"

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._get_s3", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/readme.txt")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            await _handler(event, {})

    async def test_docx_parsed_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DOCX files are parsed via parse_docx and emitted in the inline envelope."""
        docx_bytes: bytes = _make_docx_bytes()

        async def mock_download(s3_client: Any, bucket: str, key: str) -> bytes:
            return docx_bytes

        published: list[dict[str, Any]] = []
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._get_s3", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/policy.docx")
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        assert len(published) == 1
        assert published[0]["detail_type"] == "DocumentParsed"
        assert "inline" in published[0]["detail"]["payload"]
