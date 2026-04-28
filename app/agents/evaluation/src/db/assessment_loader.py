"""File-based loader for per-category assessment input.

Reads a single JSON file from ``app/agents/evaluation/data/`` and surfaces it
as a typed ``(list[QuestionItem], category_url)`` tuple. The file matches the
shape documented in ``files/system_input_output.md``.

This module is the read-side stand-in while the Postgres assessment schema is
TBC; see ``src.db.questions_repo.fetch_assessment_by_category`` for the future
async DB-backed equivalent.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from src.agents.schemas import QuestionItem
from src.config import LocalRunnerConfig
from src.utils.exceptions import UnknownCategoryError


class _AssessmentDetail(BaseModel):
    """Internal model mirroring a single question record in the input JSON."""

    uuid: str
    question: str
    reference: str
    source_excerpt: str | None = None
    timestamp: str | None = None


class _AssessmentFile(BaseModel):
    """Internal model mirroring the on-disk assessment file shape."""

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
    """Load the assessment input file for ``category`` from ``data_dir``.

    The filename is sourced from ``LocalRunnerConfig.assessment_filename``
    (configured in ``config.yaml``). The file is validated against the
    internal ``_AssessmentFile`` Pydantic model and its ``category`` checked
    case-insensitively against the requested ``category``.

    Args:
        category: The category name to look up (e.g. ``"Security"``).
        data_dir: Directory containing the assessment file. Defaults to
            ``app/agents/evaluation/data/`` relative to this module.

    Returns:
        A ``(questions, category_url)`` tuple. ``questions`` is a list of
        ``QuestionItem`` objects pairing each checklist question with its
        authoritative reference identifier; ``category_url`` is the file-level
        URL echoed back into every assessment row's ``Reference.url`` field.

    Raises:
        UnknownCategoryError: If the configured file is missing or its
            ``category`` does not match the requested category.
        pydantic.ValidationError: If the file fails validation -- malformed
            input is fail-fast.
    """
    target_dir: Path = data_dir if data_dir is not None else _default_data_dir()
    runner_config: LocalRunnerConfig = LocalRunnerConfig()
    path: Path = target_dir / runner_config.assessment_filename

    if not path.is_file():
        raise UnknownCategoryError(
            f"Assessment input file not found at {path} "
            f"(configured via local_runner.assessment_filename)."
        )

    raw: str = path.read_text(encoding="utf-8")
    parsed: _AssessmentFile = _AssessmentFile.model_validate_json(raw)

    if parsed.category.lower() != category.lower():
        raise UnknownCategoryError(
            f"Assessment file at {path} has category={parsed.category!r}, requested {category!r}."
        )

    items: list[QuestionItem] = [
        QuestionItem(question=detail.question, reference=detail.reference)
        for detail in parsed.details
    ]
    return items, parsed.url
