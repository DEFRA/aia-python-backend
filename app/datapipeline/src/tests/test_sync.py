from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


from app.datapipeline.src.sync import get_sync_record, is_changed, upsert_sync_record
from app.datapipeline.src.utils import url_to_hash

_URL = "https://defra.sharepoint.com/teams/T1/SitePages/Policy.aspx"
_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_SIZE = 4200


class TestIsChanged:
    def test_no_sync_record_is_changed(self) -> None:
        assert is_changed(None, _TS, _SIZE) is True

    def test_no_sync_record_no_timestamp_is_changed(self) -> None:
        assert is_changed(None, None, _SIZE) is True

    def test_both_timestamps_none_same_size_not_changed(self) -> None:
        sync = {"last_modified": None, "content_size": _SIZE}
        assert is_changed(sync, None, _SIZE) is False

    def test_both_timestamps_none_different_size_is_changed(self) -> None:
        sync = {"last_modified": None, "content_size": _SIZE}
        assert is_changed(sync, None, _SIZE + 1) is True

    def test_both_timestamps_none_stored_size_none_is_changed(self) -> None:
        sync = {"last_modified": None, "content_size": None}
        assert is_changed(sync, None, _SIZE) is True

    def test_stored_none_remote_has_value_is_changed(self) -> None:
        sync = {"last_modified": None, "content_size": _SIZE}
        assert is_changed(sync, _TS, _SIZE) is True

    def test_remote_none_stored_has_value_is_changed(self) -> None:
        sync = {"last_modified": _TS, "content_size": _SIZE}
        assert is_changed(sync, None, _SIZE) is True

    def test_different_timestamps_is_changed(self) -> None:
        other_ts = datetime(2024, 7, 1, tzinfo=timezone.utc)
        sync = {"last_modified": _TS, "content_size": _SIZE}
        assert is_changed(sync, other_ts, _SIZE) is True

    def test_same_timestamp_same_size_not_changed(self) -> None:
        sync = {"last_modified": _TS, "content_size": _SIZE}
        assert is_changed(sync, _TS, _SIZE) is False

    def test_same_timestamp_different_size_is_changed(self) -> None:
        sync = {"last_modified": _TS, "content_size": _SIZE}
        assert is_changed(sync, _TS, _SIZE + 500) is True

    def test_same_timestamp_stored_size_none_is_changed(self) -> None:
        # Old sync record without content_size — force re-process to capture it
        sync = {"last_modified": _TS, "content_size": None}
        assert is_changed(sync, _TS, _SIZE) is True


class TestGetSyncRecord:
    def _make_cursor(self, row: dict | None) -> MagicMock:
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = row
        return cursor

    def test_returns_none_when_no_row(self) -> None:
        conn = MagicMock()
        conn.cursor.return_value = self._make_cursor(None)
        assert get_sync_record(conn, _URL) is None

    def test_returns_row_dict(self) -> None:
        row = {
            "url_hash": url_to_hash(_URL),
            "source_url": _URL,
            "last_modified": _TS,
            "content_size": _SIZE,
        }
        conn = MagicMock()
        conn.cursor.return_value = self._make_cursor(row)
        result = get_sync_record(conn, _URL)
        assert result == row

    def test_queries_by_url_hash(self) -> None:
        conn = MagicMock()
        cursor = self._make_cursor(None)
        conn.cursor.return_value = cursor
        get_sync_record(conn, _URL)
        executed_sql = cursor.execute.call_args[0][0]
        assert "url_hash" in executed_sql
        assert cursor.execute.call_args[0][1] == (url_to_hash(_URL),)


class TestUpsertSyncRecord:
    def _make_conn(self) -> MagicMock:
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cursor
        return conn

    def test_commits_after_upsert(self) -> None:
        conn = self._make_conn()
        upsert_sync_record(conn, _URL, _TS, _SIZE, "doc-uuid-123")
        conn.commit.assert_called_once()

    def test_passes_correct_params_to_execute(self) -> None:
        conn = self._make_conn()
        upsert_sync_record(conn, _URL, _TS, _SIZE, "doc-uuid-123")
        params = conn.cursor.return_value.execute.call_args[0][1]
        assert params[0] == url_to_hash(_URL)  # url_hash
        assert params[1] == _URL  # source_url
        assert params[2] == _TS  # last_modified
        assert params[3] == _SIZE  # content_size
        assert params[4] == "doc-uuid-123"  # policy_doc_id

    def test_sql_includes_content_size(self) -> None:
        conn = self._make_conn()
        upsert_sync_record(conn, _URL, _TS, _SIZE, "doc-uuid-123")
        sql = conn.cursor.return_value.execute.call_args[0][0]
        assert "content_size" in sql
        assert "ON CONFLICT" in sql.upper()

    def test_sql_does_not_include_file_name(self) -> None:
        conn = self._make_conn()
        upsert_sync_record(conn, _URL, _TS, _SIZE, "doc-uuid-123")
        sql = conn.cursor.return_value.execute.call_args[0][0]
        assert "file_name" not in sql
