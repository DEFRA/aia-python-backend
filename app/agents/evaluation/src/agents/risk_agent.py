"""Risk management specialist agent for document-based assessment."""

import json
import logging
from typing import cast

import anthropic
from anthropic import APIError
from anthropic.types import Message, TextBlock

from src.agents.prompts.risk import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.agents.schemas import AgentResult, AssessmentRow, FinalSummary, LLMResponseMeta
from src.config import RiskAgentConfig
from src.utils.helpers import strip_code_fences

logger: logging.Logger = logging.getLogger(__name__)


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


def _format_questions_block(questions: list[str]) -> str:
    """Format a list of questions into a numbered string block.

    Args:
        questions: Ordered list of checklist question strings.

    Returns:
        A single string with each question on its own numbered line.
    """
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))


class RiskAgent:
    """Async Claude agent that assesses a document against a risk management checklist.

    Sends a document and a set of checklist questions to Claude and parses
    the structured JSON response into typed Pydantic models.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        agent_config: RiskAgentConfig,
    ) -> None:
        """Initialise the agent with an Anthropic client and configuration.

        Args:
            client: An authenticated AsyncAnthropic client instance.
            agent_config: Configuration controlling model, token limit, and temperature.
        """
        self.client: anthropic.AsyncAnthropic = client
        self.agent_config: RiskAgentConfig = agent_config

    async def assess(
        self,
        document: str,
        questions: list[str],
    ) -> AgentResult:
        """Run a risk management assessment of a document against a checklist.

        Args:
            document: Full text of the document to assess.
            questions: Ordered list of checklist questions to evaluate against.

        Returns:
            An AgentResult containing per-question assessments, a final summary,
            and API response metadata.

        Raises:
            APIError: If the Anthropic API call fails.
            ValueError: If the Claude response cannot be parsed into the expected schema.
        """
        user_content: str = USER_PROMPT_TEMPLATE.format(
            document=document,
            questions=_format_questions_block(questions),
        )

        try:
            response: Message = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=self.agent_config.max_tokens,
                temperature=self.agent_config.temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except APIError as exc:
            logger.error("Claude API error during risk assessment: %s", exc)
            raise

        meta: LLMResponseMeta = _extract_response_meta(response, self.agent_config.model)
        raw_text: str = cast(TextBlock, response.content[0]).text

        try:
            cleaned: str = strip_code_fences(raw_text)
            payload: dict[str, object] = json.loads(cleaned)
            risk_block: dict[str, object] = payload["Risk"]  # type: ignore[assignment]
            assessments: list[AssessmentRow] = [
                AssessmentRow.model_validate(row)
                for row in risk_block["Assessments"]  # type: ignore[attr-defined]
            ]
            final_summary: FinalSummary | None = (
                FinalSummary.model_validate(risk_block["Final_Summary"])
                if "Final_Summary" in risk_block
                else None
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "Failed to parse Claude response. raw_text=%.200s error=%s",
                raw_text,
                exc,
            )
            raise ValueError(f"Could not parse assessment response: {exc}") from exc

        logger.info(
            "Risk assessment complete: %d questions, %d input / %d output tokens",
            len(assessments),
            meta.input_tokens,
            meta.output_tokens,
        )

        return AgentResult(assessments=assessments, metadata=meta, final_summary=final_summary)
