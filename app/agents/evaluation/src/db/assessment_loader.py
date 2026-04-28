"""File-based loader for per-category assessment input.

Reads JSON files from ``app/agents/evaluation/data/`` and surfaces them as a
typed ``(list[QuestionItem], category_url)`` tuple. Each file matches the shape
documented in ``files/system_input_output.md``.

This module is the read-side stand-in while the Postgres assessment schema is
TBC; see ``src.db.questions_repo.fetch_assessment_by_category`` for the future
async DB-backed equivalent.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from src.agents.schemas import QuestionItem
from src.utils.exceptions import UnknownCategoryError


class _AssessmentDetail(BaseModel):
    """Internal model mirroring a single question record in the input JSON."""

    uuid: str
    question: str
    reference: str
    source_excerpt: str | None = None
    timestamp: str | None = None


class _AssessmentFile(BaseModel):
    """Internal model mirroring the on-disk ``sample_*.json`` file shape."""

    uuid: str
    url: str
    category: str
    details: list[_AssessmentDetail]


def _default_data_dir() -> Path:
    """Return the project-relative ``app/agents/evaluation/data/`` directory.

    Resolved from this module's location so the loader works regardless of the
    caller's current working directory.
    """
    return Path(__file__).resolve().parents[2] / "data"


def load_assessment_from_file(
    category: str,
    data_dir: Path | None = None,
) -> tuple[list[QuestionItem], str]:
    """Load an assessment input file for ``category`` from ``data_dir``.

    Iterates every ``*.json`` file in ``data_dir``, validates each against the
    internal ``_AssessmentFile`` Pydantic model, and returns the first whose
    ``category`` matches ``category`` case-insensitively.

    Args:
        category: The category name to look up (e.g. ``"Security"``).
        data_dir: Directory to scan for ``*.json`` files. Defaults to
            ``app/agents/evaluation/data/`` relative to this module.

    Returns:
        A ``(questions, category_url)`` tuple. ``questions`` is a list of
        ``QuestionItem`` objects pairing each checklist question with its
        authoritative reference identifier; ``category_url`` is the file-level
        URL echoed back into every assessment row's ``Reference.url`` field.

    Raises:
        UnknownCategoryError: If no file in ``data_dir`` matches ``category``.
        pydantic.ValidationError: If any file in ``data_dir`` fails to parse
            against the internal model -- malformed input is fail-fast.
    """
    target_dir: Path = data_dir if data_dir is not None else _default_data_dir()
    wanted: str = category.lower()

    for path in sorted(target_dir.glob("*.json")):
        raw: str = path.read_text(encoding="utf-8")
        parsed: _AssessmentFile = _AssessmentFile.model_validate_json(raw)
        if parsed.category.lower() == wanted:
            items: list[QuestionItem] = [
                QuestionItem(question=detail.question, reference=detail.reference)
                for detail in parsed.details
            ]
            return items, parsed.url

    raise UnknownCategoryError(
        f"No assessment input file found for category '{category}' in {target_dir}."
    )
