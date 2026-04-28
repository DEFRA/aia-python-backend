"""Tests for the file-based assessment loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.agents.schemas import QuestionItem
from src.db.assessment_loader import load_assessment_from_file
from src.utils.exceptions import UnknownCategoryError

_SAMPLE_URL: str = "https://example.test/page"
_SAMPLE_JSON: str = (
    '{"uuid": "u1", "url": "' + _SAMPLE_URL + '", '
    '"category": "Security", "details": ['
    '{"uuid": "d1", "question": "Is X enforced?", "reference": "Ref-1"},'
    '{"uuid": "d2", "question": "Is Y monitored?", "reference": "Ref-2"}'
    "]}"
)

# Filename the loader expects (matches LocalRunnerConfig default).
_ASSESSMENT_FILENAME: str = "sample_policy_assessment.json"


@pytest.fixture
def security_data_dir(tmp_path: Path) -> Path:
    """Write a synthetic Security-category JSON to ``tmp_path`` and return it."""
    (tmp_path / _ASSESSMENT_FILENAME).write_text(_SAMPLE_JSON, encoding="utf-8")
    return tmp_path


def test_load_security_returns_question_items_and_url(security_data_dir: Path) -> None:
    """Loading a Security-category file yields typed items and a non-empty URL."""
    items, url = load_assessment_from_file("Security", data_dir=security_data_dir)

    assert isinstance(items, list)
    assert len(items) >= 1
    assert all(isinstance(item, QuestionItem) for item in items)
    assert isinstance(url, str)
    assert url != ""


def test_load_is_case_insensitive(security_data_dir: Path) -> None:
    """The category match should be case-insensitive."""
    items_upper, url_upper = load_assessment_from_file("SECURITY", data_dir=security_data_dir)
    items_lower, url_lower = load_assessment_from_file("security", data_dir=security_data_dir)

    assert len(items_upper) == len(items_lower)
    assert url_upper == url_lower


def test_unknown_category_raises_unknown_category_error(tmp_path: Path) -> None:
    """Requesting an absent category raises UnknownCategoryError."""
    with pytest.raises(UnknownCategoryError):
        load_assessment_from_file("NonExistent", data_dir=tmp_path)


def test_malformed_file_raises_validation_error(tmp_path: Path) -> None:
    """A malformed assessment file at the configured path raises ValidationError."""
    bad: Path = tmp_path / _ASSESSMENT_FILENAME
    bad.write_text('{"not": "the right shape"}', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_assessment_from_file("Security", data_dir=tmp_path)


def test_explicit_data_dir_with_valid_file(tmp_path: Path) -> None:
    """Passing data_dir with a valid file should load from there."""
    sample = tmp_path / _ASSESSMENT_FILENAME
    sample.write_text(
        (
            '{"uuid": "u1", "url": "https://example.test/page", '
            '"category": "Security", "details": ['
            '{"uuid": "d1", "question": "Is X enforced?", '
            '"reference": "Ref-1"}'
            "]}"
        ),
        encoding="utf-8",
    )

    items, url = load_assessment_from_file("Security", data_dir=tmp_path)

    assert len(items) == 1
    assert items[0].question == "Is X enforced?"
    assert items[0].reference == "Ref-1"
    assert url == "https://example.test/page"
