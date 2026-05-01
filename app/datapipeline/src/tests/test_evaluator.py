from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.datapipeline.src.evaluator import QuestionExtractor, _strip_fences
from app.datapipeline.src.schemas import ExtractedQuestion

_VALID_JSON = json.dumps(
    [
        {
            "question_text": "Does the system encrypt data at rest?",
            "reference": "Section 3.2",
            "source_excerpt": "All data must be encrypted at rest using AES-256.",
        }
    ]
)

_POLICY_URL = "https://defra.sharepoint.com/teams/T1/SitePages/Security.aspx"


class TestStripFences:
    def test_no_fences_unchanged(self) -> None:
        raw = '[{"a": 1}]'
        assert _strip_fences(raw) == raw

    def test_strips_json_fence(self) -> None:
        raw = '```json\n[{"a": 1}]\n```'
        assert _strip_fences(raw) == '[{"a": 1}]'

    def test_strips_plain_fence(self) -> None:
        raw = '```\n[{"a": 1}]\n```'
        assert _strip_fences(raw) == '[{"a": 1}]'

    def test_strips_leading_trailing_whitespace(self) -> None:
        raw = '  \n[{"a": 1}]\n  '
        assert _strip_fences(raw) == '[{"a": 1}]'


def _make_extractor() -> QuestionExtractor:
    return QuestionExtractor(
        aws_access_key="key",
        aws_secret_key="secret",
        aws_region="eu-west-2",
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    )


def _make_llm_response(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


class TestQuestionExtractorExtract:
    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_returns_extracted_questions(self, mock_bedrock_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(_VALID_JSON)

        extractor = _make_extractor()
        questions = extractor.extract(_POLICY_URL, "Policy content here", "security")

        assert len(questions) == 1
        q = questions[0]
        assert isinstance(q, ExtractedQuestion)
        assert q.question_text == "Does the system encrypt data at rest?"
        assert q.reference == "Section 3.2"
        assert q.source_excerpt == "All data must be encrypted at rest using AES-256."

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_handles_json_fences_in_response(self, mock_bedrock_cls: MagicMock) -> None:
        fenced = f"```json\n{_VALID_JSON}\n```"
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(fenced)

        extractor = _make_extractor()
        questions = extractor.extract(_POLICY_URL, "content", "security")

        assert len(questions) == 1

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_raises_on_invalid_json(self, mock_bedrock_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response("not json at all")

        extractor = _make_extractor()
        with pytest.raises(ValueError, match="invalid JSON"):
            extractor.extract(_POLICY_URL, "content", "security")

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_raises_when_response_is_object_not_array(
        self, mock_bedrock_cls: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(
            '{"key": "value"}'
        )

        extractor = _make_extractor()
        with pytest.raises(ValueError, match="Expected a JSON array"):
            extractor.extract(_POLICY_URL, "content", "security")

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_calls_llm_with_temperature_zero(self, mock_bedrock_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(_VALID_JSON)

        extractor = _make_extractor()
        extractor.extract(_POLICY_URL, "content", "security")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["temperature"] == 0.0

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_content_truncated_to_8000_chars(self, mock_bedrock_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(_VALID_JSON)

        long_content = "x" * 20_000
        extractor = _make_extractor()
        extractor.extract(_POLICY_URL, long_content, "security")

        messages = mock_client.messages.create.call_args[1]["messages"]
        user_message = messages[0]["content"]
        assert "x" * 8001 not in user_message
        assert "x" * 8000 in user_message

    @patch("app.datapipeline.src.evaluator.AnthropicBedrock")
    def test_category_hint_included_in_prompt(
        self, mock_bedrock_cls: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_bedrock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_llm_response(_VALID_JSON)

        extractor = _make_extractor()
        extractor.extract(_POLICY_URL, "content", "technical")

        messages = mock_client.messages.create.call_args[1]["messages"]
        user_message = messages[0]["content"]
        assert "technical" in user_message
