from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


from app.datapipeline.src.sync import get_sync_record, is_changed, upsert_sync_record
from app.datapipeline.src.utils import url_to_hash

_URL = "https://defra.sharepoint.com/teams/T1/SitePages/Policy.aspx"
_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestIsChanged:
    def test_no_sync_record_is_changed(self) -> None:
        assert is_changed(None, _TS) is True

    def test_no_sync_record_no_timestamp_is_changed(self) -> None:
        assert is_changed(None, None) is True

    def test_both_none_timestamps_not_changed(self) -> None:
        sync = {"last_modified": None}
        assert is_changed(sync, None) is False

    def test_stored_none_remote_has_value_is_changed(self) -> None:
        sync = {"last_modified": None}
        assert is_changed(sync, _TS) is True

    def test_remote_none_stored_has_value_is_changed(self) -> None:
        sync = {"last_modified": _TS}
        assert is_changed(sync, None) is True

    def test_same_timestamp_not_changed(self) -> None:
        sync = {"last_modified": _TS}
        assert is_changed(sync, _TS) is False

    def test_different_timestamps_is_changed(self) -> None:
        other_ts = datetime(2024, 7, 1, tzinfo=timezone.utc)
        sync = {"last_modified": _TS}
        assert is_changed(sync, other_ts) is True


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
        row = {"url_hash": url_to_hash(_URL), "source_url": _URL, "last_modified": _TS}
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
    def test_commits_after_upsert(self) -> None:
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cursor

        upsert_sync_record(conn, _URL, "Policy.aspx", _TS, "doc-uuid-123")

        conn.commit.assert_called_once()

    def test_passes_correct_hash_to_execute(self) -> None:
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cursor

        upsert_sync_record(conn, _URL, "Policy.aspx", _TS, "doc-uuid-123")

        params = cursor.execute.call_args[0][1]
        assert params[0] == url_to_hash(_URL)
        assert params[1] == _URL
        assert params[2] == "Policy.aspx"
        assert params[3] == _TS
        assert params[4] == "doc-uuid-123"
