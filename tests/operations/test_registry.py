"""Tests for LinkedIn operations registry.

Validates that captured operations have required fields populated.
"""

import pytest
from linkedin.operations.registry import REGISTRY, Operation


class TestRegistryPopulated:
    """Validate registry entries have required fields."""

    def test_fetch_profile_posts_has_query_id(self):
        """fetchProfilePosts must have a non-empty query_id with correct format."""
        op = REGISTRY.get("fetchProfilePosts")
        assert op is not None, "fetchProfilePosts not found in registry"
        assert op.query_id, "fetchProfilePosts.query_id is empty"
        assert "." in op.query_id, "fetchProfilePosts.query_id must contain a dot (voyager<Service>.<hash>)"
        assert len(op.query_id) > 20, "fetchProfilePosts.query_id appears too short"

    def test_fetch_profile_posts_variables_schema(self):
        """fetchProfilePosts must have proper variables_schema."""
        op = REGISTRY.get("fetchProfilePosts")
        assert op.variables_schema, "fetchProfilePosts.variables_schema is empty"
        assert "profileUrn" in op.variables_schema, "profileUrn missing from variables_schema"
        assert "count" in op.variables_schema, "count missing from variables_schema"
        assert "start" in op.variables_schema, "start missing from variables_schema"

    def test_fetch_profile_posts_response_path(self):
        """fetchProfilePosts must have response_path set to data.posts."""
        op = REGISTRY.get("fetchProfilePosts")
        assert op.response_path == "data.posts", f"fetchProfilePosts.response_path should be 'data.posts', got {repr(op.response_path)}"

    def test_fetch_profile_posts_pagination(self):
        """fetchProfilePosts must use offset pagination strategy."""
        op = REGISTRY.get("fetchProfilePosts")
        assert op.pagination_strategy == "offset", f"fetchProfilePosts.pagination_strategy should be 'offset', got {repr(op.pagination_strategy)}"

    def test_fetch_profile_posts_status(self):
        """fetchProfilePosts must have captured status."""
        op = REGISTRY.get("fetchProfilePosts")
        assert op.status == "CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION", f"fetchProfilePosts.status should be 'CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION', got {repr(op.status)}"