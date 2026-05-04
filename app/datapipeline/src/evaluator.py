from __future__ import annotations

import json
import logging
from pathlib import Path

from anthropic import AnthropicBedrock

from app.datapipeline.src.schemas import ExtractedQuestion

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped the JSON in them."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) >= 2 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    return text.strip()


class QuestionExtractor:
    """Calls Anthropic via Bedrock to extract structured questions from policy content."""

    _SYSTEM_PROMPT: str = _load_prompt("policy_evaluation_prompt.md")

    def __init__(
        self,
        aws_access_key: str,
        aws_secret_key: str,
        aws_region: str,
        model_id: str,
        aws_session_token: str | None = None,
    ) -> None:
        self._model_id = model_id
        self._client = AnthropicBedrock(
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
            aws_session_token=aws_session_token,
            aws_region=aws_region,
        )

    def extract(
        self,
        policy_url: str,
        content: str,
        category: str,
    ) -> list[ExtractedQuestion]:
        """Call the LLM and return validated ExtractedQuestion objects.

        Args:
            policy_url: Source URL — included in the prompt for traceability.
            content: Text content extracted from the SharePoint page or document.
            category: Primary category from the policy source record (e.g. "security").
                      Passed as a hint so the LLM assigns categories accurately.

        Returns:
            List of ExtractedQuestion instances parsed from the LLM response.

        Raises:
            ValueError: If the LLM response cannot be parsed as a JSON array
                or fails Pydantic validation.
        """
        user_message = (
            f"Policy URL: {policy_url}\n"
            f"Primary category hint: {category}\n\n"
            "--- POLICY CONTENT START ---\n"
            f"{content[:8000]}\n"
            "--- POLICY CONTENT END ---\n\n"
            "Generate evaluation questions for the policy content above."
        )

        logger.info("Calling LLM model=%s policy_url=%s", self._model_id, policy_url)
        response = self._client.messages.create(
            model=self._model_id,
            max_tokens=8192,
            temperature=0.0,
            system=self._SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = _strip_fences(response.content[0].text)

        try:
            items: list[dict] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "JSON parse failed policy_url=%s error=%s raw=%.300s",
                policy_url,
                exc,
                raw,
            )
            raise ValueError(
                f"LLM returned invalid JSON for {policy_url}: {exc}"
            ) from exc

        if not isinstance(items, list):
            raise ValueError(
                f"Expected a JSON array from LLM, got {type(items).__name__}"
            )

        questions = [ExtractedQuestion.model_validate(item) for item in items]
        logger.info(
            "Extracted %d question(s) policy_url=%s", len(questions), policy_url
        )
        return questions
