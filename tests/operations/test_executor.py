"""Tests for LinkedIn operation executor."""

import pytest
import json
from unittest.mock import MagicMock, patch

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


def mock_response_with_json(json_data, status=200):
    """Create a mock response that returns JSON."""
    response = MagicMock()
    response.status = status
    response.body = lambda: json.dumps(json_data).encode()
    return response


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
    """Test fetch_profile_posts executor wiring."""

    def _mock_operation(self):
        """Return a properly configured Operation for fetchProfilePosts."""
        from linkedin.operations.registry import Operation
        return Operation(
            name="fetchProfilePosts",
            query_id="voyagerFeedDashProfilePosts.fake123",
            method="GET",
            variables_schema={"profileUrn": "str", "start": "int", "count": "int"},
            pagination_strategy="offset",
            response_path="data.posts",
            status="CAPTURED",
        )

    @patch("linkedin.operations.executor.get_operation")
    def test_returns_parsed_profile_posts(self, mock_get_operation):
        """Executor returns parsed profile posts from fixture-like response."""
        mock_get_operation.return_value = self._mock_operation()

        fixture_response = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:123,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Test post from executor",
                        "actorName": "Executor Test User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }

        client = MagicMock()
        client.get.return_value = mock_response_with_json(fixture_response)

        executor = LinkedInOperationExecutor(client)
        result = executor.fetch_profile_posts("test-vanity", start=0, count=10)

        assert result.failure is None
        assert len(result.data) == 1
        post = result.data[0]
        assert post.urn == "urn:li:fsd_update:(urn:li:activity:123,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"
        assert post.author_name == "Executor Test User"
        assert post.text == "Test post from executor"
        # Verify new schema fields exist (from Task A)
        assert hasattr(post, "author_urn")
        assert hasattr(post, "published_at")

    @patch("linkedin.operations.executor.get_operation")
    def test_propagates_response_shape_changed(self, mock_get_operation):
        """Executor propagates RESPONSE_SHAPE_CHANGED when response shape is wrong."""
        mock_get_operation.return_value = self._mock_operation()

        # Response missing 'posts' key → parser returns RESPONSE_SHAPE_CHANGED
        bad_response = {"data": {}}

        client = MagicMock()
        client.get.return_value = mock_response_with_json(bad_response)

        executor = LinkedInOperationExecutor(client)
        result = executor.fetch_profile_posts("test-vanity", start=0, count=10)

        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED
        assert len(result.data) == 0

    def test_returns_needs_recon_when_not_captured(self):
        """When registry entry has no query_id, executor returns NEEDS_RECON."""
        from linkedin.operations.registry import Operation
        mock_op = Operation(
            name="fetchProfilePosts",
            query_id="",
            method="GET",
            variables_schema={},
            status="INFERRED_NOT_CAPTURED",
        )

        with patch("linkedin.operations.executor.get_operation", return_value=mock_op):
            executor = LinkedInOperationExecutor(mock_client())
            result = executor.fetch_profile_posts("test-vanity")
            assert result.failure == FailureType.NEEDS_RECON


class TestFetchPostDetail:
    def test_returns_inferred(self):
        executor = LinkedInOperationExecutor(mock_client())
        result = executor.fetch_post_detail("urn:li:activity:123")
        assert result.failure == FailureType.INFERRED_FROM_COMPANY_POSTS
