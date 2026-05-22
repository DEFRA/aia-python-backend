"""Utility helpers for parsing LLM text responses."""

import json
import logging
import re
from typing import cast

logger: logging.Logger = logging.getLogger(__name__)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a string."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def extract_json_array(text: str) -> str:
    """Extract the first balanced JSON array from text."""
    start: int = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found in response text")

    depth: int = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unterminated JSON array in response text")


def _sanitize_llm_json(text: str) -> str:
    """Escape literal control characters inside JSON string values."""
    result: list[str] = []
    in_string: bool = False
    escape_next: bool = False

    _CONTROL_ESCAPES: dict[str, str] = {
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\b": "\\b",
        "\f": "\\f",
    }

    for char in text:
        if escape_next:
            result.append(char)
            escape_next = False
        elif char == "\\":
            result.append(char)
            escape_next = True
        elif char == '"':
            in_string = not in_string
            result.append(char)
        elif in_string and char in _CONTROL_ESCAPES:
            result.append(_CONTROL_ESCAPES[char])
        else:
            result.append(char)

    return "".join(result)


def parse_llm_json(raw_text: str) -> dict[str, object]:
    """Parse a JSON object from raw LLM output with automatic repair fallback."""
    cleaned: str = strip_code_fences(raw_text)

    try:
        return cast(dict[str, object], json.loads(cleaned))
    except json.JSONDecodeError as first_exc:
        logger.debug(
            "Initial JSON parse failed (%s); attempting control-character sanitization.",
            first_exc,
        )

    sanitized: str = _sanitize_llm_json(cleaned)
    return cast(dict[str, object], json.loads(sanitized))
