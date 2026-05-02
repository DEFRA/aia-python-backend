"""Technical compliance assessment agent.

Mirrors ``SecurityAgent`` exactly: the only differences are the prompt files,
the config class, and the top-level JSON key (``"Technical"``).
"""

import json
import logging
from pathlib import Path
from typing import cast

import anthropic
from anthropic import APIError
from anthropic.types import Message, TextBlock

from src.agents.schemas import (
    AgentResult,
    AssessmentRow,
    FinalSummary,
    LLMResponseMeta,
    QuestionItem,
)
from src.config import TechnicalAgentConfig
from src.utils.helpers import strip_code_fences

logger: logging.Logger = logging.getLogger(__name__)

_PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"
_SYSTEM_PROMPT: str = (_PROMPTS_DIR / "technical_system.md").read_text(encoding="utf-8")
_USER_TEMPLATE: str = (_PROMPTS_DIR / "technical_user.md").read_text(encoding="utf-8")


def _extract_response_meta(response: Message, model: str) -> LLMResponseMeta:
    """Build an LLMResponseMeta from a raw Anthropic API response.

    Args:
        response: The raw Message returned by the Anthropic API.
        model: The model identifier string used for the request.

    Returns:
        An LLMResponseMeta instance populated with token counts and stop reason.
    """
    return LLMResponseMeta(
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        stop_reason=response.stop_reason,
    )


def _format_questions_block(questions: list[QuestionItem]) -> str:
    """Format checklist items into a numbered XML block.

    Each item is rendered as ``<question reference="...">...</question>`` so the
    LLM sees the per-question reference identifier alongside the question text
    and can echo it back into the output ``Reference`` field.

    Args:
        questions: Ordered list of ``QuestionItem`` objects.

    Returns:
        A single string with each question on its own numbered line.
    """
    return "\n".join(
        f'{i}. <question reference="{item.reference}">{item.question}</question>'
        for i, item in enumerate(questions, start=1)
    )


class TechnicalAgent:
    """Async LLM agent that assesses a document against a technical compliance checklist.

    Sends a document and a set of checklist questions to the LLM and parses
    the structured JSON response into typed Pydantic models. Covers the technical
    implementation of DPA 2018, UK GDPR, and public-sector records-management
    obligations.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        agent_config: TechnicalAgentConfig,
    ) -> None:
        """Initialise the agent with an Anthropic client and configuration.

        Args:
            client: An authenticated AsyncAnthropic client instance.
            agent_config: Configuration controlling model, token limit, and temperature.
        """
        self.client: anthropic.AsyncAnthropic = client
        self.agent_config: TechnicalAgentConfig = agent_config

    async def assess(
        self,
        document: str,
        questions: list[QuestionItem],
        policy_doc_url: str,
    ) -> AgentResult:
        """Run a technical compliance assessment of a document against a checklist.

        Args:
            document: Full text of the document to assess.
            questions: Ordered list of ``QuestionItem`` objects pairing each
                checklist question with its authoritative reference identifier.
            policy_doc_url: SharePoint reference URL for the policy document,
                echoed into every assessment row's ``Reference.url`` field.

        Returns:
            An AgentResult containing per-question assessments, a final summary,
            and API response metadata.

        Raises:
            APIError: If the LLM API call fails.
            ValueError: If the LLM response cannot be parsed into the expected schema.
        """
        user_content: str = _USER_TEMPLATE.format(
            document=document,
            questions=_format_questions_block(questions),
            category_url=policy_doc_url,
        )

        try:
            response: Message = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=self.agent_config.max_tokens,
                temperature=self.agent_config.temperature,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except APIError as exc:
            logger.error("LLM API error during assessment: %s", exc)
            raise

        meta: LLMResponseMeta = _extract_response_meta(response, self.agent_config.model)
        raw_text: str = cast(TextBlock, response.content[0]).text

        try:
            cleaned: str = strip_code_fences(raw_text)
            payload: dict[str, object] = json.loads(cleaned)
            technical_block: dict[str, object] = payload["Technical"]  # type: ignore[assignment]
            assessments: list[AssessmentRow] = [
                AssessmentRow.model_validate(row)
                for row in cast(list[object], technical_block["Assessments"])
            ]
            final_summary: FinalSummary | None = (
                FinalSummary.model_validate(technical_block["Final_Summary"])
                if "Final_Summary" in technical_block
                else None
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "Failed to parse LLM response. raw_text=%.200s error=%s",
                raw_text,
                exc,
            )
            raise ValueError(f"Could not parse assessment response: {exc}") from exc

        logger.info(
            "Assessment complete: %d questions, %d input / %d output tokens",
            len(assessments),
            meta.input_tokens,
            meta.output_tokens,
        )

        return AgentResult(assessments=assessments, metadata=meta, final_summary=final_summary)
