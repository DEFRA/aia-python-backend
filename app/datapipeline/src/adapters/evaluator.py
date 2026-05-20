from __future__ import annotations

import json
import logging
import tenacity
from pathlib import Path

from anthropic import Anthropic, AnthropicBedrock
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    RetryError,
)

from ..domain.schemas import ExtractedQuestion

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
        max_retries: int = 3,
        retry_wait_seconds: float = 2.0,
    ) -> None:
        self._model_id = model_id
        self._provider = provider
        self._max_retries = max_retries
        self._retry_wait_seconds = retry_wait_seconds

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

    # Pricing per million tokens (USD) — update if rates change.
    # https://aws.amazon.com/bedrock/pricing/  /  https://anthropic.com/pricing
    _PRICING: dict[str, dict[str, float]] = {
        # Bedrock model IDs
        "anthropic.claude-3-7-sonnet-20250219-v1:0": {"input": 3.00, "output": 15.00},
        "anthropic.claude-3-5-sonnet-20241022-v2:0": {"input": 3.00, "output": 15.00},
        "anthropic.claude-3-5-haiku-20241022-v1:0": {"input": 0.80, "output": 4.00},
        # Direct Anthropic API model IDs
        "claude-3-7-sonnet-20250219": {"input": 3.00, "output": 15.00},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
        "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    }

    def _log_usage(self, policy_url: str, usage: object) -> None:
        """Log token counts and estimated USD cost for one LLM call."""
        input_tokens: int = getattr(usage, "input_tokens", 0)
        output_tokens: int = getattr(usage, "output_tokens", 0)
        rates = self._PRICING.get(self._model_id, {"input": 0.0, "output": 0.0})
        cost = (
            input_tokens * rates["input"] + output_tokens * rates["output"]
        ) / 1_000_000
        logger.info(
            "LLM usage  url=%s  input_tokens=%d  output_tokens=%d  "
            "total_tokens=%d  estimated_cost=$%.6f",
            policy_url,
            input_tokens,
            output_tokens,
            input_tokens + output_tokens,
            cost,
        )

    # ---------------------------------------------------------------------------
    # Internal LLM call helpers
    # ---------------------------------------------------------------------------

    def _call_llm(self, user_message: str) -> object:
        """Issue a single blocking call to the LLM and return the raw response."""
        return self._client.messages.create(
            model=self._model_id,
            max_tokens=8192,
            temperature=0.0,
            system=self._SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

    def _call_llm_with_retry(self, user_message: str, policy_url: str) -> object:
        """Call _call_llm with exponential-backoff retries on transient errors.

        Retryable exceptions:
          - Exception subclasses that are *not* ValueError / json.JSONDecodeError
            (those indicate a bad response, not a transient failure).

        Configuration is driven by the instance attributes set in __init__:
          ``self._max_retries``      — total attempts (default 3)
          ``self._retry_wait_seconds`` — initial wait before 1st retry (default 2 s)

        On exhaustion a ``tenacity.RetryError`` is raised, which the caller
        (``extract``) converts to a plain ``RuntimeError`` for cleaner logging.
        """
        # Build a fresh retry decorator each call so instance-level config is
        # respected at runtime (avoids class-level decoration issues).
        _retry = retry(
            reraise=False,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._retry_wait_seconds,
                min=self._retry_wait_seconds,
                max=self._retry_wait_seconds * 16,
            ),
            retry=retry_if_exception_type(Exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )

        @_retry
        def _attempt() -> object:
            return self._call_llm(user_message)

        try:
            return _attempt()
        except RetryError as exc:
            last = exc.last_attempt.exception()
            raise RuntimeError(
                f"LLM call failed after {self._max_retries} attempt(s) "
                f"for url={policy_url}: {last}"
            ) from last

    def extract(
        self,
        policy_url: str,
        content: str,
        category: str,
    ) -> tuple[list[ExtractedQuestion], dict]:
        """Call the LLM and return validated ExtractedQuestion objects plus usage info.

        Args:
            policy_url: Source URL — included in the prompt for traceability.
            content: Text content extracted from the SharePoint page or document.
            category: Primary category from the policy source record (e.g. "security").
                      Passed as a hint so the LLM assigns categories accurately.

        Returns:
            Tuple of (questions, usage) where usage contains:
              input_tokens, output_tokens, total_tokens, estimated_cost_usd.

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

        logger.info(
            "Calling LLM model=%s policy_url=%s (max_retries=%d, retry_wait=%.1fs)",
            self._model_id,
            policy_url,
            self._max_retries,
            self._retry_wait_seconds,
        )
        response = self._call_llm_with_retry(user_message, policy_url)
        self._log_usage(policy_url, response.usage)
        input_tokens: int = getattr(response.usage, "input_tokens", 0)
        output_tokens: int = getattr(response.usage, "output_tokens", 0)
        rates = self._PRICING.get(self._model_id, {"input": 0.0, "output": 0.0})
        cost = (
            input_tokens * rates["input"] + output_tokens * rates["output"]
        ) / 1_000_000
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": cost,
        }
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
        return questions, usage
