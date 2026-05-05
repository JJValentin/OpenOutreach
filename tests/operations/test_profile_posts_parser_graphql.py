"""Tests for ProfilePostsParser GraphQL response format (FS-3).

This module tests that the parser handles the feedDashProfileUpdatesByMemberShareFeed
response shape that LinkedIn now uses instead of the legacy data.posts format.
"""

import pytest
from linkedin.operations.parsers import ProfilePostsParser
from linkedin.operations.health import FailureType


class TestProfilePostsParserGraphQL:
    """Test ProfilePostsParser against the new GraphQL feed format."""

    def test_parse_graphql_format_extracts_posts(self):
        """Parser extracts posts from feedDashProfileUpdatesByMemberShareFeed.*elements + included."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": [
                            "urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                            "urn:li:fsd_update:(urn:li:activity:7200001,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        ],
                        "paging": {
                            "count": 10,
                            "start": 0,
                            "total": 42,
                            "hasMore": True,
                        }
                    }
                },
                "included": [
                    {
                        "$type": "com.linkedin.voyager.dash.feed.Update",
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "First post from GraphQL",
                        "actorName": "Joshua Valentin",
                        "authorUrn": "urn:li:fsd_profile:ACoAABCPpPYBP2UJsb0r9X03h42CE6c5eu2sVuM",
                        "created": {"time": 1746393600000},
                    },
                    {
                        "$type": "com.linkedin.voyager.dash.feed.Update",
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7200001,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Second post from GraphQL",
                        "actorName": "Joshua Valentin",
                        "authorUrn": "urn:li:fsd_profile:ACoAABCPpPYBP2UJsb0r9X03h42CE6c5eu2sVuM",
                        "created": {"time": 1746307200000},
                    },
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None, f"Expected no failure, got {result.failure}: {result.failure_message}"
        assert len(result.data) == 2, f"Expected 2 posts, got {len(result.data)}"
        assert result.data[0].text == "First post from GraphQL"
        assert result.data[0].author_name == "Joshua Valentin"
        assert result.data[1].text == "Second post from GraphQL"

    def test_parse_graphql_format_pagination(self):
        """Parser extracts pagination from feedDashProfileUpdatesByMemberShareFeed.paging."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": ["urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"],
                        "paging": {
                            "count": 10,
                            "start": 0,
                            "total": 42,
                            "hasMore": True,
                        }
                    }
                },
                "included": [
                    {
                        "$type": "com.linkedin.voyager.dash.feed.Update",
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Test post",
                        "actorName": "Test User",
                        "created": {"time": 1746393600000},
                    },
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert result.pagination.has_more is True
        assert result.pagination.total == 42

    def test_parse_graphql_format_empty_elements(self):
        """Parser handles empty *elements array."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": [],
                        "paging": {
                            "count": 10,
                            "start": 0,
                            "total": 0,
                            "hasMore": False,
                        }
                    }
                },
                "included": []
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 0

    def test_parse_graphql_format_resolves_included_by_entity_urn(self):
        """Parser correctly resolves *elements URNs against the included array."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": [
                            "urn:li:fsd_update:(urn:li:activity:999,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        ],
                        "paging": {"count": 10, "start": 0, "total": 1, "hasMore": False}
                    }
                },
                "included": [
                    {
                        "$type": "com.linkedin.voyager.dash.feed.Update",
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:999,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Resolved post via included array",
                        "actorName": "Resolved Author",
                        "created": {"time": 1746393600000},
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].text == "Resolved post via included array"
        assert result.data[0].author_name == "Resolved Author"

    def test_parse_legacy_format_still_works(self):
        """Parser still handles legacy data.posts format (backward compat)."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Legacy post",
                        "actorName": "Legacy User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].text == "Legacy post"

    def test_parse_no_included_returns_empty(self):
        """Parser handles missing included array gracefully."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": ["urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"],
                        "paging": {"count": 10, "start": 0, "total": 1, "hasMore": False}
                    }
                }
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 0

    def test_parse_published_at_from_created_time(self):
        """Parser extracts published_at from created.time (epoch ms)."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "data": {
                    "feedDashProfileUpdatesByMemberShareFeed": {
                        "*elements": ["urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"],
                        "paging": {"count": 10, "start": 0, "total": 1, "hasMore": False}
                    }
                },
                "included": [
                    {
                        "$type": "com.linkedin.voyager.dash.feed.Update",
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7200000,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Post with timestamp",
                        "actorName": "Test User",
                        "created": {"time": 1746393600000},
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].published_at is not None