from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


from app.datapipeline.src.db import (
    delete_policy_document_by_url,
    delete_questions_for_doc,
    fetch_all_policy_sources,
    fetch_policy_sources,
    insert_policy_document,
    insert_questions,
    load_local_policy_sources,
)
from app.datapipeline.src.schemas import ExtractedQuestion, PolicySource


def _make_cursor(rows: list[dict] | dict | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    if isinstance(rows, list):
        cursor.fetchall.return_value = rows
    else:
        cursor.fetchone.return_value = rows
    return cursor


def _make_conn(cursor: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


class TestFetchPolicySources:
    def test_returns_policy_source_list(self) -> None:
        rows = [
            {
                "url_id": 1,
                "url": "https://sp.com/teams/T1/SitePages/P.aspx",
                "filename": "Data Policy",
                "category": "security",
                "type": "page",
                "isactive": True,
            }
        ]
        conn = _make_conn(_make_cursor(rows))
        sources = fetch_policy_sources(conn)

        assert len(sources) == 1
        assert isinstance(sources[0], PolicySource)
        assert sources[0].url == "https://sp.com/teams/T1/SitePages/P.aspx"
        assert sources[0].category == "security"

    def test_returns_empty_list_when_no_rows(self) -> None:
        conn = _make_conn(_make_cursor([]))
        assert fetch_policy_sources(conn) == []

    def test_queries_active_only(self) -> None:
        cursor = _make_cursor([])
        conn = _make_conn(cursor)
        fetch_policy_sources(conn)

        sql = cursor.execute.call_args[0][0]
        assert "isactive" in sql.lower()


class TestInsertPolicyDocument:
    def test_returns_policy_doc_id(self) -> None:
        returned_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cursor = _make_cursor({"policy_doc_id": returned_id})
        cursor.fetchone.return_value = (returned_id,)
        conn = _make_conn(cursor)

        result = insert_policy_document(
            conn, "https://sp.com/page", "page.aspx", "security"
        )

        assert result == returned_id

    def test_commits_after_insert(self) -> None:
        cursor = _make_cursor()
        cursor.fetchone.return_value = ("some-uuid",)
        conn = _make_conn(cursor)

        insert_policy_document(conn, "https://sp.com/page", "page.aspx", "security")

        conn.commit.assert_called_once()

    def test_uses_on_conflict_upsert(self) -> None:
        cursor = _make_cursor()
        cursor.fetchone.return_value = ("some-uuid",)
        conn = _make_conn(cursor)

        insert_policy_document(conn, "https://sp.com/page", "page.aspx", "security")

        sql = cursor.execute.call_args[0][0]
        assert "ON CONFLICT" in sql.upper()


class TestInsertQuestions:
    def _make_questions(self, n: int = 2) -> list[ExtractedQuestion]:
        return [
            ExtractedQuestion(
                question_text=f"Question {i}?",
                reference=f"Section {i}",
                source_excerpt=f"Excerpt {i}",
            )
            for i in range(n)
        ]

    def test_returns_count_of_inserted_questions(self) -> None:
        cursor = _make_cursor()
        conn = _make_conn(cursor)

        questions = self._make_questions(3)
        count = insert_questions(conn, "doc-uuid-123", questions)

        assert count == 3

    def test_commits_after_batch(self) -> None:
        cursor = _make_cursor()
        conn = _make_conn(cursor)

        insert_questions(conn, "doc-uuid-123", self._make_questions(1))

        conn.commit.assert_called_once()

    def test_inserts_one_row_per_question(self) -> None:
        cursor = _make_cursor()
        conn = _make_conn(cursor)

        questions = [
            ExtractedQuestion(
                question_text="Q1?", reference="Sec 1", source_excerpt="Excerpt 1"
            ),
            ExtractedQuestion(
                question_text="Q2?", reference="Sec 2", source_excerpt="Excerpt 2"
            ),
        ]
        insert_questions(conn, "doc-uuid-123", questions)

        execute_calls = cursor.execute.call_args_list
        question_inserts = [
            c for c in execute_calls if "data_pipeline.questions" in str(c)
        ]
        assert len(question_inserts) == 2

    def test_empty_questions_list_returns_zero(self) -> None:
        cursor = _make_cursor()
        conn = _make_conn(cursor)

        count = insert_questions(conn, "doc-uuid-123", [])

        assert count == 0
        conn.commit.assert_called_once()


class TestFetchAllPolicySources:
    def test_returns_all_sources_including_inactive(self) -> None:
        rows = [
            {
                "url_id": 1,
                "url": "https://sp.com/active",
                "filename": "Active",
                "category": "security",
                "type": "page",
                "isactive": True,
            },
            {
                "url_id": 2,
                "url": "https://sp.com/inactive",
                "filename": "Inactive",
                "category": "technical",
                "type": "page",
                "isactive": False,
            },
        ]
        conn = _make_conn(_make_cursor(rows))
        sources = fetch_all_policy_sources(conn)

        assert len(sources) == 2
        assert sources[0].isactive is True
        assert sources[1].isactive is False

    def test_returns_empty_list_when_no_rows(self) -> None:
        conn = _make_conn(_make_cursor([]))
        assert fetch_all_policy_sources(conn) == []

    def test_does_not_filter_by_isactive(self) -> None:
        cursor = _make_cursor([])
        conn = _make_conn(cursor)
        fetch_all_policy_sources(conn)

        sql = cursor.execute.call_args[0][0]
        assert "WHERE" not in sql.upper()


class TestDeletePolicyDocumentByUrl:
    def test_returns_one_when_row_deleted(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 1
        conn = _make_conn(cursor)

        count = delete_policy_document_by_url(conn, "https://sp.com/page")

        assert count == 1

    def test_returns_zero_when_no_matching_row(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 0
        conn = _make_conn(cursor)

        count = delete_policy_document_by_url(conn, "https://sp.com/missing")

        assert count == 0

    def test_commits_after_delete(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 1
        conn = _make_conn(cursor)

        delete_policy_document_by_url(conn, "https://sp.com/page")

        conn.commit.assert_called_once()

    def test_deletes_by_source_url(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 0
        conn = _make_conn(cursor)

        delete_policy_document_by_url(conn, "https://sp.com/page")

        sql, params = cursor.execute.call_args[0]
        assert "policy_documents" in sql
        assert "source_url" in sql
        assert params == ("https://sp.com/page",)


class TestDeleteQuestionsForDoc:
    def test_executes_delete_and_commits(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 5
        conn = _make_conn(cursor)

        count = delete_questions_for_doc(conn, "doc-uuid-abc")

        assert count == 5
        conn.commit.assert_called_once()

    def test_delete_sql_targets_correct_table(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 0
        conn = _make_conn(cursor)

        delete_questions_for_doc(conn, "doc-uuid-abc")

        sql = cursor.execute.call_args[0][0]
        assert "data_pipeline.questions" in sql
        assert "policy_doc_id" in sql

    def test_returns_zero_when_no_questions_exist(self) -> None:
        cursor = _make_cursor()
        cursor.rowcount = 0
        conn = _make_conn(cursor)

        count = delete_questions_for_doc(conn, "doc-uuid-abc")

        assert count == 0


_SAMPLE_SOURCES = [
    {
        "url_id": 1,
        "url": "https://defra.sharepoint.com/teams/T1/SitePages/Policy.aspx",
        "filename": "Policy Page",
        "category": "security",
        "type": "page",
        "isactive": True,
    },
    {
        "url_id": 2,
        "url": "https://defra.sharepoint.com/teams/T1/SitePages/Inactive.aspx",
        "filename": "Inactive Page",
        "category": "technical",
        "type": "page",
        "isactive": False,
    },
]


class TestLoadLocalPolicySources:
    def _write_file(self, data: list[dict]) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(data, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_returns_active_sources_only(self) -> None:
        path = self._write_file(_SAMPLE_SOURCES)
        sources = load_local_policy_sources(path)
        assert len(sources) == 1
        assert (
            sources[0].url
            == "https://defra.sharepoint.com/teams/T1/SitePages/Policy.aspx"
        )

    def test_returns_policy_source_instances(self) -> None:
        path = self._write_file(_SAMPLE_SOURCES)
        sources = load_local_policy_sources(path)
        assert all(isinstance(s, PolicySource) for s in sources)

    def test_returns_empty_when_all_inactive(self) -> None:
        data = [{**_SAMPLE_SOURCES[1]}]  # only the inactive one
        path = self._write_file(data)
        assert load_local_policy_sources(path) == []

    def test_accepts_path_string(self) -> None:
        path = self._write_file([_SAMPLE_SOURCES[0]])
        sources = load_local_policy_sources(str(path))
        assert len(sources) == 1

    def test_bundled_file_is_valid(self) -> None:
        # tests/ -> src/ -> datapipeline/ -> data/
        bundled = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "policy_sources.json"
        )
        sources = load_local_policy_sources(bundled)
        assert len(sources) > 0
        assert all(isinstance(s, PolicySource) for s in sources)
