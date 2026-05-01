from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import msal
import requests

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
}


def extract_sharepoint_parts(url: str) -> tuple[str, str]:
    """Return (hostname, site_path) from a SharePoint URL.

    Supports /teams/... and /sites/... URL patterns.
    Example:
        https://defra.sharepoint.com/teams/Team3221/SitePages/...
        -> ("defra.sharepoint.com", "/teams/Team3221")
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url}")

    parts = parsed.path.split("/")
    if "teams" in parts:
        idx = parts.index("teams")
    elif "sites" in parts:
        idx = parts.index("sites")
    else:
        raise ValueError(f"Cannot determine site root for URL: {url}")

    site_path = "/" + "/".join(parts[idx : idx + 2])
    return hostname, site_path


def _extract_page_name(url: str) -> str | None:
    """Return the .aspx filename when the URL points to a SitePages page.

    Returns None for document library URLs (e.g. /Forms/AllItems.aspx) and
    non-page URLs so the caller knows to fall back to site metadata.
    """
    path = unquote(urlparse(url).path)
    lower = path.lower()
    if "sitepages/" in lower:
        idx = lower.index("sitepages/") + len("sitepages/")
        segment = path[idx:].split("/")[0]
        if segment.lower().endswith(".aspx"):
            return segment
    return None


def _html_to_text(html: str) -> str:
    """Strip HTML tags and decode common entities to plain text."""
    html = re.sub(
        r"<(style|script)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = _HTML_TAG_RE.sub(" ", html)
    for entity, char in _HTML_ENTITIES.items():
        text = text.replace(entity, char)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _extract_canvas_text(page: dict) -> str:
    """Extract plain text from a sitePage canvasLayout web parts."""
    canvas = page.get("canvasLayout") or {}
    parts: list[str] = []
    for section in canvas.get("horizontalSections", []):
        for col in section.get("columns", []):
            for wp in col.get("webparts", []):
                text = _html_to_text(wp.get("innerHtml") or "")
                if text:
                    parts.append(text)
    return "\n\n".join(parts)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class SharePointClient:
    """Fetches page content from SharePoint Online via Microsoft Graph API."""

    _SCOPES = ["https://graph.microsoft.com/.default"]

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._authority = f"https://login.microsoftonline.com/{tenant_id}"

    def _get_access_token(self) -> str:
        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=self._client_secret,
        )
        result = app.acquire_token_for_client(scopes=self._SCOPES)
        if "access_token" not in result:
            raise RuntimeError(
                f"MSAL token acquisition failed: {result.get('error_description', result)}"
            )
        return result["access_token"]

    def _get(self, url: str, headers: dict, label: str) -> dict:
        logger.info("Graph API: %s", label)
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(
                f"Graph API error {response.status_code} [{label}]: {response.text}"
            )
        return response.json()

    @staticmethod
    def _is_client_error(exc: requests.exceptions.RequestException) -> bool:
        """Return True for 4xx responses — permanent failures, no point retrying."""
        return "Graph API error 4" in str(exc)

    def _get_with_retry(
        self,
        url: str,
        headers: dict,
        label: str,
        max_retries: int = 1,
        backoff: float = 2.0,
    ) -> dict:
        """Call _get with up to max_retries additional attempts on transient failure.

        4xx client errors are not retried — they are permanent.
        Waits backoff * 2^attempt seconds between attempts (exponential backoff).
        Raises the last exception if all attempts are exhausted.
        """
        last_exc: Exception
        for attempt in range(max_retries + 1):
            try:
                return self._get(url, headers, label)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if self._is_client_error(exc) or attempt >= max_retries:
                    break
                wait = backoff * (2**attempt)
                logger.warning(
                    "Graph API attempt %d/%d failed for [%s], retrying in %.0fs: %s",
                    attempt + 1,
                    max_retries + 1,
                    label,
                    wait,
                    exc,
                )
                time.sleep(wait)
        raise last_exc

    def _fetch_page_by_list(
        self, site_id: str, page_name: str, headers: dict
    ) -> dict | None:
        """Find a SitePage by listing all pages and matching by name, then fetch canvasLayout.

        Used as a fallback when the OData $filter query returns 400 (some sites do
        not support $filter on the pages endpoint).  Makes two extra calls:
          1. List all pages with id + name only ($select avoids large payloads).
          2. Fetch the matched page individually with ?$expand=canvasLayout.
        Returns None if no page with the given name is found.
        """
        list_data = self._get(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/pages/microsoft.graph.sitePage?$select=id,name",
            headers,
            f"page list for {page_name}",
        )
        matched = next(
            (p for p in list_data.get("value", []) if p.get("name") == page_name),
            None,
        )
        if not matched:
            return None
        page_id = matched["id"]
        return self._get(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/pages/{page_id}/microsoft.graph.sitePage?$expand=canvasLayout",
            headers,
            f"page detail {page_name}",
        )

    def read_page_content(self, url: str) -> tuple[str, datetime | None]:
        """Fetch SharePoint page text and return (text_content, last_modified).

        For SitePages URLs: fetches full page body via canvasLayout (Graph v1.0).
          - Primary: OData $filter by name with canvasLayout expand.
          - Fallback A: if $filter returns 400, list all pages and match by name
            (some sites do not support $filter on the pages endpoint).
          - Fallback B: site title + description when no page content is available.
        For other URLs (document libraries, PDFs): uses site metadata directly.
        last_modified is the page-level timestamp when available, site-level otherwise.

        Args:
            url: SharePoint page URL.

        Returns:
            (text_content, last_modified)

        Raises:
            requests.exceptions.RequestException: On HTTP error fetching site metadata.
            ValueError: If the URL cannot be parsed into a SharePoint site path.
        """
        hostname, site_path = extract_sharepoint_parts(url)
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        # Step 1 — get site ID (always required)
        site_data = self._get(
            f"https://graph.microsoft.com/v1.0/sites/{hostname}:{site_path}",
            headers,
            f"site metadata {site_path}",
        )
        site_id = site_data["id"]

        # Step 2 — fetch actual page content for SitePages URLs
        page_name = _extract_page_name(url)
        if page_name:
            page = self._fetch_page_content(site_id, page_name, headers)
            if page:
                title = page.get("title") or ""
                body = _extract_canvas_text(page)
                text_content = f"{title}\n\n{body}".strip() if body else title
                last_modified = _parse_timestamp(page.get("lastModifiedDateTime"))
                logger.info(
                    "Fetched page content length=%d last_modified=%s page=%s",
                    len(text_content),
                    last_modified,
                    page_name,
                )
                return text_content, last_modified

        # Fallback — site title + description
        title = site_data.get("displayName") or site_data.get("title") or ""
        description = site_data.get("description") or ""
        text_content = f"{title}\n{description}".strip()
        last_modified = _parse_timestamp(site_data.get("lastModifiedDateTime"))
        logger.info(
            "Fetched site metadata (fallback) length=%d last_modified=%s",
            len(text_content),
            last_modified,
        )
        return text_content, last_modified

    def _fetch_page_content(
        self, site_id: str, page_name: str, headers: dict
    ) -> dict | None:
        """Fetch a single SitePage with canvasLayout.

        Tries OData $filter first; falls back to list-and-match if $filter returns 400.
        Returns None when the page cannot be retrieved (logs the reason).
        """
        filter_url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/pages/microsoft.graph.sitePage"
            f"?$filter=name eq '{page_name}'&$expand=canvasLayout"
        )
        try:
            data = self._get_with_retry(
                filter_url, headers, f"page content {page_name}"
            )
            pages = data.get("value", [])
            return pages[0] if pages else None
        except requests.exceptions.RequestException as exc:
            if "Graph API error 400" in str(exc):
                logger.warning(
                    "OData $filter returned 400 for [%s] — trying list-and-match fallback",
                    page_name,
                )
                try:
                    return self._fetch_page_by_list(site_id, page_name, headers)
                except requests.exceptions.RequestException as list_exc:
                    logger.error(
                        "List-and-match also failed for [%s]: %s", page_name, list_exc
                    )
                    return None
            logger.warning(
                "Page content fetch failed for [%s], falling back to site metadata: %s",
                page_name,
                exc,
            )
            return None
