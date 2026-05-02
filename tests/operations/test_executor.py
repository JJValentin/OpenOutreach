"""Tests for LinkedIn operation executor."""

import pytest
from unittest.mock import MagicMock

from linkedin.operations.executor import LinkedInOperationExecutor
from linkedin.operations.health import FailureType


def mock_client():
    """Create a mock PlaywrightLinkedinAPI."""
    client = MagicMock()
    response = MagicMock()
    response.status = 200
    response.body = lambda: b'{"data": {"organizationPageUpdateV2": {"paging": {"count": 10, "start": 0, "total": 0}, "*elements": [], "$type": "CollectionResponse"}}}}'
    client.get.return_value = response
    return client


class TestResolvePostUrn:
    def test_activity_urn_from_url(self):
        executor = LinkedInOperationExecutor(mock_client())
        urn = executor.resolve_post_urn("https://www.linkedin.com/feed/update/urn:li:activity:123456789/")
        assert urn == "urn:li:activity:123456789"

    def test_posts_url_format(self):
        executor = LinkedInOperationExecutor(mock_client())
        urn = executor.resolve_post_urn("https://www.linkedin.com/posts/company_linkedin_activity-123456789-abc/")
        assert "123456789" in urn

    def test_share_urn_from_url(self):
        executor = LinkedInOperationExecutor(mock_client())
        urn = executor.resolve_post_urn("https://www.linkedin.com/feed/update/urn:li:share:123456789/")
        assert urn == "urn:li:share:123456789"


class TestFetchProfilePosts:
    def test_returns_needs_recon(self):
        executor = LinkedInOperationExecutor(mock_client())
        result = executor.fetch_profile_posts("some-profile")
        assert result.failure == FailureType.NEEDS_RECON


class TestFetchPostDetail:
    def test_returns_inferred(self):
        executor = LinkedInOperationExecutor(mock_client())
        result = executor.fetch_post_detail("urn:li:activity:123")
        assert result.failure == FailureType.NEEDS_RECON
