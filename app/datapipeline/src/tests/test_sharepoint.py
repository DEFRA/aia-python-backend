from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from app.datapipeline.src.sharepoint import (
    SharePointClient,
    _extract_canvas_text,
    _extract_page_name,
    _html_to_text,
    extract_sharepoint_parts,
)

_TEAMS_URL = "https://defra.sharepoint.com/teams/Team3221/SitePages/DataPolicy.aspx"
_SITES_URL = "https://defra.sharepoint.com/sites/environment/SitePages/Policy.aspx"
_LIBRARY_URL = (
    "https://defra.sharepoint.com/teams/Team3221/Published%20Docs/Forms/AllItems.aspx"
)
_TS_STR = "2024-06-01T12:00:00Z"
_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_SITE_ID = "defra.sharepoint.com,site-id-111,web-id-222"


# ---------------------------------------------------------------------------
# extract_sharepoint_parts
# ---------------------------------------------------------------------------


class TestExtractSharepointParts:
    def test_teams_url(self) -> None:
        hostname, site_path = extract_sharepoint_parts(_TEAMS_URL)
        assert hostname == "defra.sharepoint.com"
        assert site_path == "/teams/Team3221"

    def test_sites_url(self) -> None:
        hostname, site_path = extract_sharepoint_parts(_SITES_URL)
        assert hostname == "defra.sharepoint.com"
        assert site_path == "/sites/environment"

    def test_raises_for_unknown_pattern(self) -> None:
        with pytest.raises(ValueError, match="Cannot determine site root"):
            extract_sharepoint_parts("https://defra.sharepoint.com/other/segment/page")

    def test_raises_for_no_hostname(self) -> None:
        with pytest.raises(ValueError, match="no hostname"):
            extract_sharepoint_parts("/relative/path/only")


# ---------------------------------------------------------------------------
# _extract_page_name
# ---------------------------------------------------------------------------


class TestExtractPageName:
    def test_sitepages_aspx(self) -> None:
        assert _extract_page_name(_TEAMS_URL) == "DataPolicy.aspx"

    def test_sitepages_with_query_params(self) -> None:
        url = "https://defra.sharepoint.com/teams/T1/SitePages/Policy.aspx?xsdata=abc"
        assert _extract_page_name(url) == "Policy.aspx"

    def test_returns_none_for_library_url(self) -> None:
        assert _extract_page_name(_LIBRARY_URL) is None

    def test_returns_none_for_pdf_url(self) -> None:
        url = "https://defra.sharepoint.com/:b:/r/teams/T1/Docs/file.pdf"
        assert _extract_page_name(url) is None

    def test_returns_none_for_root_site(self) -> None:
        url = "https://defra.sharepoint.com/teams/Team3221"
        assert _extract_page_name(url) is None


# ---------------------------------------------------------------------------
# _html_to_text
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_strips_tags(self) -> None:
        assert _html_to_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_nbsp(self) -> None:
        assert _html_to_text("a&nbsp;b") == "a b"

    def test_removes_style_block(self) -> None:
        result = _html_to_text("<style>.a{color:red}</style>text")
        assert "color" not in result
        assert "text" in result

    def test_collapses_whitespace(self) -> None:
        assert _html_to_text("a  \n\t  b") == "a b"

    def test_empty_string(self) -> None:
        assert _html_to_text("") == ""


# ---------------------------------------------------------------------------
# _extract_canvas_text
# ---------------------------------------------------------------------------


class TestExtractCanvasText:
    def test_extracts_webpart_text(self) -> None:
        page = {
            "canvasLayout": {
                "horizontalSections": [
                    {"columns": [{"webparts": [{"innerHtml": "<p>Policy text</p>"}]}]},
                    {"columns": [{"webparts": [{"innerHtml": "<p>More content</p>"}]}]},
                ]
            }
        }
        text = _extract_canvas_text(page)
        assert "Policy text" in text
        assert "More content" in text

    def test_returns_empty_when_no_canvas(self) -> None:
        assert _extract_canvas_text({}) == ""

    def test_skips_empty_webparts(self) -> None:
        page = {
            "canvasLayout": {
                "horizontalSections": [
                    {"columns": [{"webparts": [{"innerHtml": "   "}]}]}
                ]
            }
        }
        assert _extract_canvas_text(page) == ""


# ---------------------------------------------------------------------------
# SharePointClient.read_page_content
# ---------------------------------------------------------------------------


def _make_client() -> SharePointClient:
    return SharePointClient(
        tenant_id="tenant-id", client_id="client-id", client_secret="secret"
    )


def _site_response(last_modified: str | None = None) -> dict:
    data: dict = {
        "id": _SITE_ID,
        "displayName": "Site Title",
        "description": "Site description",
    }
    if last_modified:
        data["lastModifiedDateTime"] = last_modified
    return data


def _pages_response(
    page_name: str = "DataPolicy.aspx",
    title: str = "Data Policy",
    body_html: str = "<p>Full policy content here.</p>",
    last_modified: str = _TS_STR,
) -> dict:
    return {
        "value": [
            {
                "name": page_name,
                "title": title,
                "lastModifiedDateTime": last_modified,
                "canvasLayout": {
                    "horizontalSections": [
                        {"columns": [{"webparts": [{"innerHtml": body_html}]}]}
                    ]
                },
            }
        ]
    }


def _mock_get(responses: list[dict]) -> MagicMock:
    """Build a requests.get mock that returns each dict as a 200 response in order."""

    def make_resp(data: dict) -> MagicMock:
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = data
        return r

    mock = MagicMock(side_effect=[make_resp(d) for d in responses])
    return mock


class TestReadPageContent:
    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_returns_page_content_and_timestamp(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(), _pages_response(body_html="<p>Policy text.</p>")]
        ).side_effect

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Policy text" in text
        assert "Data Policy" in text
        assert last_modified == _TS

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_page_last_modified_used_over_site(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        site_ts = "2020-01-01T00:00:00Z"
        page_ts = "2024-06-01T12:00:00Z"
        mock_get.side_effect = _mock_get(
            [
                _site_response(last_modified=site_ts),
                _pages_response(last_modified=page_ts),
            ]
        ).side_effect

        client = _make_client()
        _, last_modified = client.read_page_content(_TEAMS_URL)

        assert last_modified == _TS  # page timestamp, not site timestamp

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_falls_back_to_site_metadata_for_library_url(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(last_modified=_TS_STR)]
        ).side_effect

        client = _make_client()
        text, last_modified = client.read_page_content(_LIBRARY_URL)

        assert "Site Title" in text
        assert last_modified == _TS
        assert mock_get.call_count == 1  # only site call — no pages API call

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_falls_back_when_pages_api_returns_empty(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(last_modified=_TS_STR), {"value": []}]
        ).side_effect

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Site Title" in text
        assert last_modified == _TS

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_falls_back_when_pages_api_fails(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        site_resp = MagicMock()
        site_resp.status_code = 200
        site_resp.json.return_value = _site_response(last_modified=_TS_STR)
        pages_resp = MagicMock()
        pages_resp.status_code = 403
        pages_resp.text = "Forbidden"
        # 4xx is not retried — only one pages call before fallback
        mock_get.side_effect = [site_resp, pages_resp]

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Site Title" in text  # fallback content
        assert last_modified == _TS

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_raises_when_site_call_fails(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        err_resp = MagicMock()
        err_resp.status_code = 403
        err_resp.text = "Forbidden"
        mock_get.return_value = err_resp

        client = _make_client()
        with pytest.raises(req_lib.exceptions.RequestException, match="403"):
            client.read_page_content(_TEAMS_URL)

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    def test_raises_when_token_fails(self, mock_msal: MagicMock) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "AADSTS70011",
        }
        client = _make_client()
        with pytest.raises(RuntimeError, match="MSAL token acquisition failed"):
            client.read_page_content(_TEAMS_URL)

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_site_graph_url_built_correctly(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(), _pages_response()]
        ).side_effect

        client = _make_client()
        client.read_page_content(_TEAMS_URL)

        first_call_url = mock_get.call_args_list[0][0][0]
        assert (
            "graph.microsoft.com/v1.0/sites/defra.sharepoint.com:/teams/Team3221"
            in first_call_url
        )

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_pages_api_url_contains_page_name_and_expand(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(), _pages_response()]
        ).side_effect

        client = _make_client()
        client.read_page_content(_TEAMS_URL)

        second_call_url = mock_get.call_args_list[1][0][0]
        assert "microsoft.graph.sitePage" in second_call_url
        assert "DataPolicy.aspx" in second_call_url
        assert "canvasLayout" in second_call_url


# ---------------------------------------------------------------------------
# SharePointClient._get_with_retry
# ---------------------------------------------------------------------------


class TestGetWithRetry:
    def _make_timeout_exc(self) -> req_lib.exceptions.ReadTimeout:
        return req_lib.exceptions.ReadTimeout("Read timed out.")

    def _make_site_r(self, last_modified: str | None = None) -> MagicMock:
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = _site_response(last_modified=last_modified or _TS_STR)
        return r

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    @patch("app.datapipeline.src.sharepoint.time.sleep")
    def test_succeeds_on_first_attempt_without_sleep(
        self, mock_sleep: MagicMock, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = _mock_get(
            [_site_response(), _pages_response()]
        ).side_effect

        client = _make_client()
        client.read_page_content(_TEAMS_URL)

        mock_sleep.assert_not_called()

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    @patch("app.datapipeline.src.sharepoint.time.sleep")
    def test_retries_once_on_pages_timeout_then_succeeds(
        self, mock_sleep: MagicMock, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        timeout_resp = self._make_timeout_exc()
        page_r = MagicMock()
        page_r.status_code = 200
        page_r.json.return_value = _pages_response(body_html="<p>Content</p>")
        # site OK → pages timeout → pages OK on retry
        mock_get.side_effect = [self._make_site_r(), timeout_resp, page_r]

        client = _make_client()
        text, _ = client.read_page_content(_TEAMS_URL)

        assert "Content" in text
        mock_sleep.assert_called_once()
        assert mock_get.call_count == 3  # site + timeout + retry

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    @patch("app.datapipeline.src.sharepoint.time.sleep")
    def test_falls_back_after_all_retries_exhausted(
        self, mock_sleep: MagicMock, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = [
            self._make_site_r(),
            self._make_timeout_exc(),
            self._make_timeout_exc(),
        ]

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Site Title" in text
        assert last_modified == _TS
        mock_sleep.assert_called_once()  # one sleep between the two page attempts

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    @patch("app.datapipeline.src.sharepoint.time.sleep")
    def test_backoff_delay_is_two_seconds(
        self, mock_sleep: MagicMock, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        mock_get.side_effect = [
            self._make_site_r(),
            self._make_timeout_exc(),
            self._make_timeout_exc(),
        ]

        client = _make_client()
        client.read_page_content(_TEAMS_URL)

        mock_sleep.assert_called_once_with(2.0)

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    @patch("app.datapipeline.src.sharepoint.time.sleep")
    def test_4xx_not_retried(
        self, mock_sleep: MagicMock, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        bad_r = MagicMock()
        bad_r.status_code = 400
        bad_r.text = '{"error":{"code":"badArgument"}}'
        # Only two responses needed: site OK + one 400 (no retry for 4xx)
        mock_get.side_effect = [self._make_site_r(), bad_r, bad_r]

        client = _make_client()
        client.read_page_content(_TEAMS_URL)

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# SharePointClient._fetch_page_content — 400 list-and-match fallback
# ---------------------------------------------------------------------------


def _list_response(
    page_name: str = "DataPolicy.aspx", page_id: str = "page-id-1"
) -> dict:
    return {"value": [{"id": page_id, "name": page_name}]}


class TestFetchPageContentFallback:
    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_400_triggers_list_and_match_fallback(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        site_r = MagicMock()
        site_r.status_code = 200
        site_r.json.return_value = _site_response(last_modified=_TS_STR)
        bad_r = MagicMock()
        bad_r.status_code = 400
        bad_r.text = '{"error":{"code":"badArgument"}}'
        list_r = MagicMock()
        list_r.status_code = 200
        list_r.json.return_value = _list_response("DataPolicy.aspx", "pid-1")
        detail_r = MagicMock()
        detail_r.status_code = 200
        detail_r.json.return_value = _pages_response(body_html="<p>CDAP content</p>")[
            "value"
        ][0]
        mock_get.side_effect = [site_r, bad_r, list_r, detail_r]

        client = _make_client()
        text, _ = client.read_page_content(_TEAMS_URL)

        assert "CDAP content" in text
        assert mock_get.call_count == 4  # site + 400 filter + list + detail

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_400_list_finds_no_match_falls_back_to_site_metadata(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        site_r = MagicMock()
        site_r.status_code = 200
        site_r.json.return_value = _site_response(last_modified=_TS_STR)
        bad_r = MagicMock()
        bad_r.status_code = 400
        bad_r.text = '{"error":{"code":"badArgument"}}'
        # list returns pages but none match the target name
        list_r = MagicMock()
        list_r.status_code = 200
        list_r.json.return_value = {"value": [{"id": "x", "name": "OtherPage.aspx"}]}
        mock_get.side_effect = [site_r, bad_r, list_r]

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Site Title" in text
        assert last_modified == _TS

    @patch("app.datapipeline.src.sharepoint.msal.ConfidentialClientApplication")
    @patch("app.datapipeline.src.sharepoint.requests.get")
    def test_400_list_also_fails_falls_back_to_site_metadata(
        self, mock_get: MagicMock, mock_msal: MagicMock
    ) -> None:
        mock_msal.return_value.acquire_token_for_client.return_value = {
            "access_token": "tok"
        }
        site_r = MagicMock()
        site_r.status_code = 200
        site_r.json.return_value = _site_response(last_modified=_TS_STR)
        bad_r = MagicMock()
        bad_r.status_code = 400
        bad_r.text = '{"error":{"code":"badArgument"}}'
        list_err_r = MagicMock()
        list_err_r.status_code = 403
        list_err_r.text = "Forbidden"
        mock_get.side_effect = [site_r, bad_r, list_err_r]

        client = _make_client()
        text, last_modified = client.read_page_content(_TEAMS_URL)

        assert "Site Title" in text
        assert last_modified == _TS
