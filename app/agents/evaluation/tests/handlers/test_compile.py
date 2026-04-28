"""Tests for the Stage 7 compile handler's display-name surface.

Plan 11 removes ``compile.py`` entirely; until then the display-name table is
the smallest stable contract worth pinning so a stale entry can't quietly leak
into the rendered report.
"""

from __future__ import annotations

from src.handlers.compile import AGENT_DISPLAY_NAMES


def test_compile_display_names_match_two_agents() -> None:
    """``AGENT_DISPLAY_NAMES`` must map exactly the two surviving agent types."""
    assert set(AGENT_DISPLAY_NAMES.keys()) == {"security", "governance"}


def test_compile_display_names_are_human_readable() -> None:
    """Each display name should be a non-empty, capitalised label suitable for headings."""
    for label in AGENT_DISPLAY_NAMES.values():
        assert label
        assert label[0].isupper()


def test_compile_governance_display_name_reflects_information_governance() -> None:
    """The governance agent's display label should make its UK IG remit explicit.

    Avoids the bare label "Governance" which downstream readers (auditors,
    risk reviewers) could misread as corporate or technical governance.
    """
    assert AGENT_DISPLAY_NAMES["governance"] == "Information Governance"
