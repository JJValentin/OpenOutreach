"""Tests for linkedin/api/client.py — PlaywrightLinkedinAPI fetch shim."""
from unittest.mock import MagicMock

import pytest

from linkedin.api.client import PlaywrightLinkedinAPI


class MockAccountSession:
    """Minimal stand-in for AccountSession with mocked context/page."""
    def __init__(self, cookies=None):
        self.context = MagicMock()
        self.context.cookies.return_value = cookies or []
        self.page = MagicMock()


def test_fetch_shim_headers_include_origin_and_referer():
    """
    Assert that PlaywrightLinkedinAPI constructs headers with:
      - Origin == "https://www.linkedin.com"
      - Referer starts with "https://www.linkedin.com/"
      - csrf-token strips quotes from JSESSIONID
      - existing headers remain present
    """
    mock_session = MockAccountSession(cookies=[
        {"name": "JSESSIONID", "value": '"abc123"'},
        {"name": "li_at", "value": "redacted"},
    ])

    api = PlaywrightLinkedinAPI(mock_session)

    assert api.headers["Origin"] == "https://www.linkedin.com"
    assert api.headers["Referer"].startswith("https://www.linkedin.com/")
    assert api.headers["csrf-token"] == "abc123"  # quotes stripped
    assert api.headers["accept"] == "application/vnd.linkedin.normalized+json+2.1"
    assert api.headers["x-li-lang"] == "en_US"
    assert api.headers["x-restli-protocol-version"] == "2.0.0"


def test_csrf_token_strips_quotes_from_jsessionid():
    """JSESSIONID values may be wrapped in quotes; assert stripping."""
    mock_session = MockAccountSession(cookies=[
        {"name": "JSESSIONID", "value": '"quoted-value"'},
    ])
    api = PlaywrightLinkedinAPI(mock_session)
    assert api.headers["csrf-token"] == "quoted-value"


def test_csrf_token_empty_when_no_jsessionid():
    """Graceful fallback when JSESSIONID is absent."""
    mock_session = MockAccountSession(cookies=[])
    api = PlaywrightLinkedinAPI(mock_session)
    assert api.headers["csrf-token"] == ""
