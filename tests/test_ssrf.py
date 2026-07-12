"""Tests for the scrape SSRF guard."""

from __future__ import annotations

import pytest

from chimera.scrape.ssrf import check_url, is_safe_url


def test_blocks_loopback_and_metadata_and_private() -> None:
    for bad in (
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://[::1]/x",
    ):
        assert is_safe_url(bad) is False, bad
        with pytest.raises(ValueError):
            check_url(bad)


def test_blocks_non_http_schemes() -> None:
    assert is_safe_url("file:///etc/passwd") is False
    assert is_safe_url("ftp://host/x") is False


def test_allows_a_public_ip() -> None:
    check_url("http://8.8.8.8/")  # public IP, does not raise
    assert is_safe_url("https://93.184.216.34/") is True  # example.com's IP
