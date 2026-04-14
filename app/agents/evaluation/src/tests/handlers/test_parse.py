"""Tests for src/handlers/parse.py — Stage 3 Parse Lambda handler."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis_aio
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
    body: dict[str, str] = {"docId": doc_id, "s3Key": s3_key}
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
        assert body.docId == "doc-001"
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
            SqsRecordBody.model_validate({"docId": "x"})  # missing s3Key


@pytest.mark.asyncio
class TestParseHandler:
    """Integration tests for the parse handler _handler function."""

    async def test_pdf_parsed_and_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: PDF downloaded, parsed, cached in Redis, event published."""
        pdf_bytes: bytes = _make_text_pdf()
        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        # Mock S3 download
        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return pdf_bytes

        published: list[dict[str, Any]] = []

        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr(
            "src.handlers.parse._get_publisher", lambda: mock_publisher
        )
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/test.pdf")
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        # Event was published
        assert len(published) == 1
        assert published[0]["detail_type"] == "DocumentParsed"

    async def test_cache_hit_skips_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When chunks are already cached, parsing is skipped."""
        pdf_bytes: bytes = _make_text_pdf()
        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        # Pre-populate cache
        import hashlib

        content_hash: str = hashlib.sha256(pdf_bytes).hexdigest()
        cache_key: str = f"chunks:{content_hash}"
        await fake_redis.setex(cache_key, 3600, json.dumps([{"chunk_index": 0, "text": "cached"}]))

        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return pdf_bytes

        published: list[dict[str, Any]] = []
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event()
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        assert len(published) == 1

    async def test_scanned_pdf_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A scanned PDF (no text layer) raises ScannedPdfError."""
        blank_doc = fitz.open()
        blank_doc.new_page()
        blank_bytes: bytes = blank_doc.tobytes()
        blank_doc.close()

        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return blank_bytes

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/scanned.pdf")

        with pytest.raises(ScannedPdfError):
            await _handler(event, {})

    async def test_unsupported_extension_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-PDF/DOCX file extension raises ValueError."""
        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return b"not a real file"

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: MagicMock())
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/readme.txt")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            await _handler(event, {})

    async def test_docx_parsed_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DOCX files are parsed via parse_docx."""
        docx_bytes: bytes = _make_docx_bytes()
        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return docx_bytes

        published: list[dict[str, Any]] = []
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(
            side_effect=lambda dt, d: published.append({"detail_type": dt, "detail": d})
        )

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(s3_key="uploads/policy.docx")
        result: dict[str, Any] = await _handler(event, {})

        assert result["statusCode"] == 200
        assert len(published) == 1
        assert published[0]["detail_type"] == "DocumentParsed"

    async def test_receipt_handle_stored_in_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SQS receipt handle is written to Redis for later deletion."""
        pdf_bytes: bytes = _make_text_pdf()
        fake_redis: fakeredis_aio.FakeRedis = fakeredis_aio.FakeRedis(decode_responses=True)

        async def mock_download(
            s3_client: Any, bucket: str, key: str
        ) -> bytes:
            return pdf_bytes

        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock()

        monkeypatch.setattr("src.handlers.parse._download_s3", mock_download)
        monkeypatch.setattr("src.handlers.parse.get_redis", AsyncMock(return_value=fake_redis))
        monkeypatch.setattr("src.handlers.parse._get_publisher", lambda: mock_publisher)
        monkeypatch.setattr("src.handlers.parse._emit_metric", AsyncMock())
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        monkeypatch.setenv("REDIS_HOST", "localhost")

        from src.handlers.parse import _handler

        event: dict[str, Any] = _make_sqs_event(doc_id="doc-receipt-test")
        await _handler(event, {})

        stored: str | None = await fake_redis.get("receipt:doc-receipt-test")
        assert stored == "test-receipt-handle-abc"
