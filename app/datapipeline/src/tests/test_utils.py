from __future__ import annotations

import hashlib


from app.datapipeline.src.utils import page_name_from_url, url_to_hash


class TestUrlToHash:
    def test_returns_sha256_hex(self) -> None:
        url = "https://defra.sharepoint.com/teams/Team3221/SitePages/Policy.aspx"
        expected = hashlib.sha256(url.encode()).hexdigest()
        assert url_to_hash(url) == expected

    def test_output_is_64_chars(self) -> None:
        assert len(url_to_hash("https://example.com/any")) == 64

    def test_deterministic(self) -> None:
        url = "https://defra.sharepoint.com/sites/env/SitePages/Doc.aspx"
        assert url_to_hash(url) == url_to_hash(url)

    def test_different_urls_produce_different_hashes(self) -> None:
        assert url_to_hash("https://example.com/a") != url_to_hash(
            "https://example.com/b"
        )


class TestPageNameFromUrl:
    def test_returns_last_path_segment(self) -> None:
        url = "https://defra.sharepoint.com/teams/T1/SitePages/DataPolicy"
        assert page_name_from_url(url) == "DataPolicy"

    def test_strips_trailing_slash(self) -> None:
        url = "https://defra.sharepoint.com/sites/env/SitePages/Security/"
        assert page_name_from_url(url) == "Security"

    def test_aspx_segment_preserved(self) -> None:
        url = "https://defra.sharepoint.com/teams/Team/SitePages/Policy.aspx"
        assert page_name_from_url(url) == "Policy.aspx"

    def test_fallback_when_no_path(self) -> None:
        assert page_name_from_url("https://example.com") == "page"

    def test_fallback_when_root_slash(self) -> None:
        assert page_name_from_url("https://example.com/") == "page"
