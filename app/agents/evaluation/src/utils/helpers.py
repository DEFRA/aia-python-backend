"""Utility helpers for parsing LLM text responses."""

import json
import logging
import re
from typing import cast

logger: logging.Logger = logging.getLogger(__name__)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a string.

    Args:
        text: Raw LLM response text that may be wrapped in ``` or ```json fences.

    Returns:
        The input text with leading and trailing code fences stripped.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def extract_json_array(text: str) -> str:
    """Extract the first balanced JSON array from text.

    Useful when an LLM response contains a JSON array embedded within
    a larger JSON object or surrounded by prose.

    Args:
        text: String that contains a JSON array, possibly surrounded by other content.

    Returns:
        The first complete JSON array substring, including its surrounding brackets.

    Raises:
        ValueError: If no array opener is found or the array is unterminated.
    """
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
    """Escape literal control characters inside JSON string values.

    LLMs occasionally embed raw newlines, carriage returns, or tabs inside
    string values, which makes the JSON invalid.  This function iterates over
    every character and, while inside a quoted string, replaces those bare
    control characters with their proper JSON escape sequences.

    Args:
        text: Raw (potentially malformed) JSON text from the LLM.

    Returns:
        The same text with in-string control characters escaped.
    """
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
    """Parse a JSON object from raw LLM output with automatic repair fallback.

    Steps:
    1. Strip markdown code fences.
    2. Try ``json.loads`` directly.
    3. If that fails, sanitize in-string control characters and retry.

    Args:
        raw_text: Raw LLM response text, optionally wrapped in code fences.

    Returns:
        Parsed JSON as a ``dict``.

    Raises:
        json.JSONDecodeError: If the text cannot be parsed even after sanitization.
    """
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
