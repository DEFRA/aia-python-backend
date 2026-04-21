"""GDPR compliance assessment agent."""
import logging
from typing import cast

import anthropic
from anthropic import APIError
from anthropic.types import Message, TextBlock

from scripts.gdpr_compliance_system_prompt import GDPR_COMPLIANCE_SYSTEM_PROMPT
from src.config import GDPRAgentConfig

logger: logging.Logger = logging.getLogger(__name__)

GDPR_USER_TEMPLATE: str = """<content>
{content}
</content>

Assess the above content for GDPR compliance. Return your response following the required output
format defined in your instructions."""


class GDPRComplianceAgent:
    """Async Claude agent that assesses text or documents for GDPR compliance.

    Returns the model's structured natural-language assessment as a dict
    alongside API response metadata.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        agent_config: GDPRAgentConfig,
    ) -> None:
        """Initialise the agent with an Anthropic client and configuration.

        Args:
            client: An authenticated AsyncAnthropic client instance.
            agent_config: Configuration controlling model, token limit, and temperature.
        """
        self.client: anthropic.AsyncAnthropic = client
        self.agent_config: GDPRAgentConfig = agent_config

    async def assess(self, content: str) -> dict[str, object]:
        """Run a GDPR compliance assessment against the supplied content.

        Args:
            content: The text, document excerpt, or data description to assess.

        Returns:
            A dict containing model metadata and the raw assessment text from Claude.
            Keys: "model", "input_tokens", "output_tokens", "stop_reason", "assessment".

        Raises:
            APIError: If the Anthropic API call fails.
        """
        user_content: str = GDPR_USER_TEMPLATE.format(content=content)

        try:
            response: Message = await self.client.messages.create(
                model=self.agent_config.model,
                max_tokens=self.agent_config.max_tokens,
                temperature=self.agent_config.temperature,
                system=GDPR_COMPLIANCE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except APIError as exc:
            logger.error("Claude API error during GDPR assessment: %s", exc)
            raise

        raw_text: str = cast(TextBlock, response.content[0]).text

        logger.info(
            "GDPR assessment complete: %d input / %d output tokens",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return {
            "model": self.agent_config.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "stop_reason": response.stop_reason,
            "assessment": raw_text,
        }
