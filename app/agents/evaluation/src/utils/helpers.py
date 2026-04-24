"""Utility helpers for parsing LLM text responses."""

import re


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
