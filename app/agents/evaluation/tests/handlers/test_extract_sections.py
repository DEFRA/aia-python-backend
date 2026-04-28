"""Tests for the Stage 5 Extract Sections Lambda handler."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest
from pydantic import ValidationError

from src.agents.schemas import DocumentTaggedDetail, QuestionItem

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_TEST_CATEGORY_URL: str = "https://example.test/category"


def _make_event(
    doc_id: str = "doc-001",
    tagged_cache_key: str = "tagged:abc123",
    content_hash: str = "abc123",
) -> dict[str, Any]:
    """Build a minimal EventBridge event dict for Stage 5."""
    detail: DocumentTaggedDetail = DocumentTaggedDetail(
        docId=doc_id,
        taggedCacheKey=tagged_cache_key,
        contentHash=content_hash,
    )
    return {"detail": detail.model_dump(by_alias=True)}


def _sample_tagged_chunks() -> list[dict[str, Any]]:
    """Return sample tagged chunks covering multiple agent types."""
    return [
        {
            "chunk_index": 0,
            "page": 1,
            "is_heading": True,
            "text": "Authentication Section",
            "relevant": False,
            "tags": [],
            "reason": None,
        },
        {
            "chunk_index": 1,
            "page": 1,
            "is_heading": False,
            "text": "MFA is enforced for all users.",
            "relevant": True,
            "tags": ["authentication"],
            "reason": "Covers MFA.",
        },
        {
            "chunk_index": 2,
            "page": 2,
            "is_heading": True,
            "text": "Data Governance Section",
            "relevant": False,
            "tags": [],
            "reason": None,
        },
        {
            "chunk_index": 3,
            "page": 2,
            "is_heading": False,
            "text": "Data classification policy is enforced.",
            "relevant": True,
            "tags": ["data_governance"],
            "reason": "Covers data classification.",
        },
        {
            "chunk_index": 4,
            "page": 3,
            "is_heading": False,
            "text": "Compliance audit runs quarterly.",
            "relevant": True,
            "tags": ["compliance"],
            "reason": "Covers compliance.",
        },
        {
            "chunk_index": 5,
            "page": 3,
            "is_heading": False,
            "text": "Irrelevant paragraph about office layout.",
            "relevant": False,
            "tags": [],
            "reason": None,
        },
        {
            "chunk_index": 6,
            "page": 4,
            "is_heading": True,
            "text": "Incident Response",
            "relevant": False,
            "tags": [],
            "reason": None,
        },
        {
            "chunk_index": 7,
            "page": 4,
            "is_heading": False,
            "text": "IR plan tested annually.",
            "relevant": True,
            "tags": ["incident_response"],
            "reason": "Covers IR.",
        },
    ]


def _mock_assessment_items() -> list[QuestionItem]:
    """Default questions used by the patched ``load_assessment_from_file``."""
    return [
        QuestionItem(question="Is MFA enabled?", reference="Ref-1"),
        QuestionItem(question="Are logs centralised?", reference="Ref-2"),
    ]


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """Create a fresh fake Redis instance per test."""
    return fakeredis.FakeRedis(decode_responses=True)


# ---------------------------------------------------------------------------
# Tests for extract_sections_for_agent()
# ---------------------------------------------------------------------------


class TestExtractSectionsForAgent:
    """Tests for the pure extract_sections_for_agent() function."""

    def test_security_agent_gets_authentication_chunks(self) -> None:
        """Security agent should include chunks tagged with authentication."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "security")

        texts: list[str] = [c["text"] for c in result]
        assert "MFA is enforced for all users." in texts

    def test_security_agent_excludes_data_governance_only(self) -> None:
        """Security agent should not include chunks tagged only with data_governance."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "security")

        texts: list[str] = [c["text"] for c in result]
        assert "Data classification policy is enforced." not in texts

    def test_heading_injection(self) -> None:
        """The nearest preceding heading should be included before a matched chunk."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "security")

        assert result[0]["is_heading"] is True
        assert result[0]["text"] == "Authentication Section"

    def test_heading_not_duplicated(self) -> None:
        """If two consecutive matched chunks share a heading, it appears only once."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = [
            {
                "chunk_index": 0,
                "page": 1,
                "is_heading": True,
                "text": "Security Controls",
                "relevant": False,
                "tags": [],
                "reason": None,
            },
            {
                "chunk_index": 1,
                "page": 1,
                "is_heading": False,
                "text": "MFA enforced.",
                "relevant": True,
                "tags": ["authentication"],
                "reason": "MFA",
            },
            {
                "chunk_index": 2,
                "page": 1,
                "is_heading": False,
                "text": "Keys rotated quarterly.",
                "relevant": True,
                "tags": ["secrets_management"],
                "reason": "Keys",
            },
        ]
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "security")

        headings: list[dict[str, Any]] = [c for c in result if c.get("is_heading")]
        assert len(headings) == 1

    def test_preserves_chunk_order(self) -> None:
        """Output chunks should preserve original document order."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "solution")

        indices: list[int] = [c["chunk_index"] for c in result]
        assert indices == sorted(indices)

    def test_data_agent_gets_compliance_and_data_governance(self) -> None:
        """Data agent should include data_governance and compliance tagged chunks."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "data")

        body_texts: list[str] = [c["text"] for c in result if not c.get("is_heading")]
        assert "Data classification policy is enforced." in body_texts
        assert "Compliance audit runs quarterly." in body_texts

    def test_risk_agent_includes_incident_response(self) -> None:
        """Risk agent should include incident_response tagged chunks."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "risk")

        body_texts: list[str] = [c["text"] for c in result if not c.get("is_heading")]
        assert "IR plan tested annually." in body_texts

    def test_irrelevant_chunks_excluded(self) -> None:
        """Chunks with relevant=False should never appear in output."""
        from src.handlers.extract_sections import extract_sections_for_agent

        chunks: list[dict[str, Any]] = _sample_tagged_chunks()
        result: list[dict[str, Any]] = extract_sections_for_agent(chunks, "solution")

        texts: list[str] = [c["text"] for c in result if not c.get("is_heading")]
        assert "Irrelevant paragraph about office layout." not in texts

    def test_empty_chunks_returns_empty(self) -> None:
        """Empty input should return empty output."""
        from src.handlers.extract_sections import extract_sections_for_agent

        result: list[dict[str, Any]] = extract_sections_for_agent([], "security")
        assert result == []


# ---------------------------------------------------------------------------
# Tests for _sections_to_text()
# ---------------------------------------------------------------------------


class TestSectionsToText:
    """Tests for the _sections_to_text() serialiser."""

    def test_heading_prefixed(self) -> None:
        """Headings should be prefixed with '## '."""
        from src.handlers.extract_sections import _sections_to_text

        sections: list[dict[str, Any]] = [
            {"is_heading": True, "text": "Authentication"},
            {"is_heading": False, "text": "MFA is enforced."},
        ]
        text: str = _sections_to_text(sections)
        assert text.startswith("## Authentication")

    def test_body_chunks_separated_by_double_newline(self) -> None:
        """Chunks should be separated by double newlines."""
        from src.handlers.extract_sections import _sections_to_text

        sections: list[dict[str, Any]] = [
            {"is_heading": False, "text": "Paragraph one."},
            {"is_heading": False, "text": "Paragraph two."},
        ]
        text: str = _sections_to_text(sections)
        assert text == "Paragraph one.\n\nParagraph two."

    def test_empty_sections_returns_empty_string(self) -> None:
        """Empty sections list should produce an empty string."""
        from src.handlers.extract_sections import _sections_to_text

        text: str = _sections_to_text([])
        assert text == ""


# ---------------------------------------------------------------------------
# Tests for _handler
# ---------------------------------------------------------------------------


def _patch_loader() -> Any:
    """Patch ``load_assessment_from_file`` to return predictable typed data."""
    return patch(
        "src.handlers.extract_sections.load_assessment_from_file",
        return_value=(_mock_assessment_items(), _TEST_CATEGORY_URL),
    )


class TestHandler:
    """Tests for the Stage 5 _handler async function."""

    @pytest.mark.asyncio
    async def test_validates_event(self) -> None:
        """_handler should reject an event missing required detail fields."""
        from src.handlers.extract_sections import _handler

        bad_event: dict[str, Any] = {"detail": {"docId": "doc-001"}}

        with pytest.raises(ValidationError):
            await _handler(bad_event, {})

    @pytest.mark.asyncio
    async def test_tagged_cache_miss_raises(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Handler should raise RuntimeError if tagged chunks are not in Redis."""
        event: dict[str, Any] = _make_event()

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
        ):
            from src.handlers.extract_sections import _handler

            with pytest.raises(RuntimeError, match="Tagged chunks cache miss"):
                await _handler(event, {})

    @pytest.mark.asyncio
    async def test_enqueues_five_sqs_messages(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Handler should enqueue exactly 5 SQS messages, one per agent type."""
        event: dict[str, Any] = _make_event()

        await fake_redis.setex(
            "tagged:abc123",
            86_400,
            json.dumps(_sample_tagged_chunks()),
        )

        sent_messages: list[dict[str, Any]] = []

        def _mock_send_message(**kwargs: Any) -> dict[str, Any]:
            sent_messages.append(kwargs)
            return {"MessageId": "mock-id"}

        mock_sqs: MagicMock = MagicMock()
        mock_sqs.send_message = _mock_send_message

        mock_emit: AsyncMock = AsyncMock()

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
            patch(
                "src.handlers.extract_sections._get_sqs",
                return_value=mock_sqs,
            ),
            patch(
                "src.handlers.extract_sections._get_s3",
                return_value=MagicMock(),
            ),
            _patch_loader(),
            patch(
                "src.handlers.extract_sections._emit_metric",
                mock_emit,
            ),
            patch.dict(
                "os.environ",
                {"SQS_TASKS_QUEUE_URL": "https://sqs.example.com/tasks"},
            ),
        ):
            from src.handlers.extract_sections import _handler

            result: dict[str, Any] = await _handler(event, {})

        expected_count: int = 5
        assert len(sent_messages) == expected_count
        expected_status: int = 200
        assert result["statusCode"] == expected_status

        agent_types_sent: set[str] = set()
        for msg in sent_messages:
            body: dict[str, Any] = json.loads(msg["MessageBody"])
            agent_types_sent.add(body["agentType"])
        assert agent_types_sent == {"security", "data", "risk", "ea", "solution"}

    @pytest.mark.asyncio
    async def test_payload_contains_category_url_and_typed_questions(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Each enqueued payload deserialises into ``AgentTaskBody`` cleanly."""
        from src.handlers.agent import AgentTaskBody

        event: dict[str, Any] = _make_event()

        await fake_redis.setex(
            "tagged:abc123",
            86_400,
            json.dumps(_sample_tagged_chunks()),
        )

        sent_messages: list[dict[str, Any]] = []

        def _mock_send_message(**kwargs: Any) -> dict[str, Any]:
            sent_messages.append(kwargs)
            return {"MessageId": "mock-id"}

        mock_sqs: MagicMock = MagicMock()
        mock_sqs.send_message = _mock_send_message

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
            patch(
                "src.handlers.extract_sections._get_sqs",
                return_value=mock_sqs,
            ),
            patch(
                "src.handlers.extract_sections._get_s3",
                return_value=MagicMock(),
            ),
            _patch_loader(),
            patch(
                "src.handlers.extract_sections._emit_metric",
                AsyncMock(),
            ),
            patch.dict(
                "os.environ",
                {"SQS_TASKS_QUEUE_URL": "https://sqs.example.com/tasks"},
            ),
        ):
            from src.handlers.extract_sections import _handler

            await _handler(event, {})

        body: AgentTaskBody = AgentTaskBody.model_validate_json(sent_messages[0]["MessageBody"])
        assert body.categoryUrl == _TEST_CATEGORY_URL
        assert all(isinstance(q, QuestionItem) for q in body.questions)
        assert body.questions[0].question == "Is MFA enabled?"
        assert body.questions[0].reference == "Ref-1"

    @pytest.mark.asyncio
    async def test_s3_offload_pointer_carries_category_url(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """When the payload is offloaded to S3, the pointer message still has categoryUrl."""
        event: dict[str, Any] = _make_event()

        large_text: str = "x" * 300_000
        large_chunks: list[dict[str, Any]] = [
            {
                "chunk_index": 0,
                "page": 1,
                "is_heading": False,
                "text": large_text,
                "relevant": True,
                "tags": ["authentication"],
                "reason": "Large chunk.",
            },
        ]

        await fake_redis.setex(
            "tagged:abc123",
            86_400,
            json.dumps(large_chunks),
        )

        sent_messages: list[dict[str, Any]] = []
        s3_puts: list[dict[str, Any]] = []

        def _mock_send_message(**kwargs: Any) -> dict[str, Any]:
            sent_messages.append(kwargs)
            return {"MessageId": "mock-id"}

        def _mock_put_object(**kwargs: Any) -> dict[str, Any]:
            s3_puts.append(kwargs)
            return {}

        mock_sqs: MagicMock = MagicMock()
        mock_sqs.send_message = _mock_send_message

        mock_s3: MagicMock = MagicMock()
        mock_s3.put_object = _mock_put_object

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
            patch(
                "src.handlers.extract_sections._get_sqs",
                return_value=mock_sqs,
            ),
            patch(
                "src.handlers.extract_sections._get_s3",
                return_value=mock_s3,
            ),
            _patch_loader(),
            patch(
                "src.handlers.extract_sections._emit_metric",
                AsyncMock(),
            ),
            patch.dict(
                "os.environ",
                {
                    "SQS_TASKS_QUEUE_URL": "https://sqs.example.com/tasks",
                    "S3_BUCKET": "test-bucket",
                },
            ),
        ):
            from src.handlers.extract_sections import _handler

            await _handler(event, {})

        pointer_messages: list[dict[str, Any]] = []
        for msg in sent_messages:
            body: dict[str, Any] = json.loads(msg["MessageBody"])
            if "s3PayloadKey" in body:
                pointer_messages.append(body)

        assert pointer_messages, "Expected at least one SQS message with s3PayloadKey"
        assert len(s3_puts) > 0, "Expected at least one S3 put_object call"

        for pointer in pointer_messages:
            assert pointer["categoryUrl"] == _TEST_CATEGORY_URL
            assert isinstance(pointer["questions"], list)
            assert "question" in pointer["questions"][0]
            assert "reference" in pointer["questions"][0]

    @pytest.mark.asyncio
    async def test_questions_cached_in_redis(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Questions loaded on first call should be cached in Redis."""
        event: dict[str, Any] = _make_event()

        await fake_redis.setex(
            "tagged:abc123",
            86_400,
            json.dumps(_sample_tagged_chunks()),
        )

        def _mock_send_message(**kwargs: Any) -> dict[str, Any]:
            return {"MessageId": "mock-id"}

        mock_sqs: MagicMock = MagicMock()
        mock_sqs.send_message = _mock_send_message

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
            patch(
                "src.handlers.extract_sections._get_sqs",
                return_value=mock_sqs,
            ),
            patch(
                "src.handlers.extract_sections._get_s3",
                return_value=MagicMock(),
            ),
            _patch_loader(),
            patch(
                "src.handlers.extract_sections._emit_metric",
                AsyncMock(),
            ),
            patch.dict(
                "os.environ",
                {"SQS_TASKS_QUEUE_URL": "https://sqs.example.com/tasks"},
            ),
        ):
            from src.handlers.extract_sections import _handler

            await _handler(event, {})

        for agent_type in ["security", "data", "risk", "ea", "solution"]:
            cached: str | None = await fake_redis.get(f"questions:{agent_type}")
            assert cached is not None, f"Expected questions cached for {agent_type}"

            parsed: dict[str, Any] = json.loads(cached)
            assert parsed["url"] == _TEST_CATEGORY_URL
            assert isinstance(parsed["questions"], list)
            assert parsed["questions"][0]["question"] == "Is MFA enabled?"
            assert parsed["questions"][0]["reference"] == "Ref-1"

    @pytest.mark.asyncio
    async def test_emits_section_count_metrics(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Handler should emit SectionCount CloudWatch metric per agent type."""
        event: dict[str, Any] = _make_event()

        await fake_redis.setex(
            "tagged:abc123",
            86_400,
            json.dumps(_sample_tagged_chunks()),
        )

        def _mock_send_message(**kwargs: Any) -> dict[str, Any]:
            return {"MessageId": "mock-id"}

        mock_sqs: MagicMock = MagicMock()
        mock_sqs.send_message = _mock_send_message

        mock_emit: AsyncMock = AsyncMock()

        with (
            patch(
                "src.handlers.extract_sections.get_redis",
                return_value=fake_redis,
            ),
            patch(
                "src.handlers.extract_sections._get_redis_config",
                return_value=MagicMock(),
            ),
            patch(
                "src.handlers.extract_sections._get_sqs",
                return_value=mock_sqs,
            ),
            patch(
                "src.handlers.extract_sections._get_s3",
                return_value=MagicMock(),
            ),
            _patch_loader(),
            patch(
                "src.handlers.extract_sections._emit_metric",
                mock_emit,
            ),
            patch.dict(
                "os.environ",
                {"SQS_TASKS_QUEUE_URL": "https://sqs.example.com/tasks"},
            ),
        ):
            from src.handlers.extract_sections import _handler

            await _handler(event, {})

        metric_calls: list[str] = [call.args[0] for call in mock_emit.call_args_list]
        section_count_calls: list[str] = [m for m in metric_calls if m == "SectionCount"]
        expected_metric_count: int = 5
        assert len(section_count_calls) == expected_metric_count
