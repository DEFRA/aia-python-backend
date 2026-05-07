from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic
from anthropic import Anthropic, AnthropicBedrock

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
    """Calls Anthropic (directly or via Bedrock) to extract structured questions.

    Provider is selected by the LLM_PROVIDER env var:
      - "bedrock"   (default) — uses AWS Bedrock; requires AWS credentials.
      - "anthropic"           — uses the direct Anthropic API; requires ANTHROPIC_API_KEY.
    """

    _SYSTEM_PROMPT: str = _load_prompt("policy_evaluation_prompt.md")

    def __init__(
        self,
        aws_region: str,
        model_id: str,
        provider: str = "bedrock",
        aws_access_key: str | None = None,
        aws_secret_key: str | None = None,
        aws_session_token: str | None = None,
        anthropic_api_key: str | None = None,
    ) -> None:
        self._model_id = model_id
        self._provider = provider

        if provider == "anthropic":
            if not anthropic_api_key:
                raise ValueError(
                    "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set"
                )
            self._client: Anthropic | AnthropicBedrock = Anthropic(
                api_key=anthropic_api_key
            )
            logger.info("LLM provider: Anthropic direct API")
        else:
            # Bedrock — only pass explicit credentials when both key + secret are present.
            # Omitting them lets the SDK fall back to the standard AWS credential chain
            # (env vars, ~/.aws/credentials, instance profile), which avoids 403s
            # from expired STS session tokens left in .env.
            kwargs: dict = {"aws_region": aws_region}
            if aws_access_key and aws_secret_key:
                kwargs["aws_access_key"] = aws_access_key
                kwargs["aws_secret_key"] = aws_secret_key
                if aws_session_token:
                    kwargs["aws_session_token"] = aws_session_token
            self._client = AnthropicBedrock(**kwargs)
            logger.info("LLM provider: AWS Bedrock region=%s", aws_region)

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
