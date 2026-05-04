"""Tests for ProfilePostsParser."""

import pytest
from urllib.parse import urlparse
from linkedin.operations.parsers import (
    ProfilePostsParser,
    get_parser,
    PARSERS,
)
from linkedin.operations.health import FailureType


class TestProfilePostsParser:
    """Test ProfilePostsParser for fetchProfilePosts operation."""

    def test_parse_empty_response(self):
        """Test parsing empty profile posts response."""
        parser = ProfilePostsParser()
        result = parser.parse({})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED
        assert len(result.data) == 0

    def test_parse_shape_changed_missing_posts(self):
        """Test parsing when posts key is missing."""
        parser = ProfilePostsParser()
        result = parser.parse({"data": {}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED

    def test_parse_valid_structure(self):
        """Test parsing valid profile posts response."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:123,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "This is a test post",
                        "actorName": "Test User",
                        "content": {},
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        post = result.data[0]
        assert post.urn == "urn:li:fsd_update:(urn:li:activity:123,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"
        assert post.author_name == "Test User"
        assert post.text == "This is a test post"

    def test_parse_multiple_posts(self):
        """Test parsing multiple profile posts."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:1,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "First post",
                        "actorName": "User One",
                        "content": {},
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    },
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:2,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Second post",
                        "actorName": "User Two",
                        "content": {},
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 2
        assert result.data[0].text == "First post"
        assert result.data[1].text == "Second post"

    def test_parse_empty_posts_array(self):
        """Test parsing when posts array is empty."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": []
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 0


class TestGetParserProfilePosts:
    """Test that get_parser works for fetchProfilePosts."""

    def test_get_parser_for_profile_posts(self):
        """Test get_parser returns ProfilePostsParser for fetchProfilePosts."""
        parser = get_parser("fetchProfilePosts")
        assert parser is not None
        assert isinstance(parser, ProfilePostsParser)


class TestProfilePostsParserFixture:
    """Test ProfilePostsParser against the sanitized fixture."""

    def test_parse_fixture_returns_parse_result(self):
        """Loading the fixture returns a ParseResult with no failure."""
        import json
        from pathlib import Path
        from linkedin.operations.parsers import ParseResult

        fixture = Path(__file__).parent.parent.parent / "linkedin" / "operations" / "fixtures" / "profile_posts.json"
        with open(fixture) as f:
            raw = json.load(f)

        parser = ProfilePostsParser()
        result = parser.parse(raw)

        assert isinstance(result, ParseResult)
        assert result.failure is None
        assert len(result.data) == 3

    def test_parse_fixture_extracts_three_urns(self):
        """Extracts 3 profile Activity/update URNs matching fixture activity_urns."""
        import json
        from pathlib import Path

        fixture = Path(__file__).parent.parent.parent / "linkedin" / "operations" / "fixtures" / "profile_posts.json"
        with open(fixture) as f:
            raw = json.load(f)

        parser = ProfilePostsParser()
        result = parser.parse(raw)

        expected_urns = raw["activity_urns"]
        actual_urns = [post.urn for post in result.data]
        assert actual_urns == expected_urns
        assert len(actual_urns) == 3

    def test_parse_fixture_text_non_empty(self):
        """All parsed posts have non-empty text."""
        import json
        from pathlib import Path

        fixture = Path(__file__).parent.parent.parent / "linkedin" / "operations" / "fixtures" / "profile_posts.json"
        with open(fixture) as f:
            raw = json.load(f)

        parser = ProfilePostsParser()
        result = parser.parse(raw)

        for post in result.data:
            assert post.text is not None
            assert len(post.text) > 0

    def test_parse_fixture_has_more_defaults_false(self):
        """has_more defaults to False when fixture has no pagination info."""
        import json
        from pathlib import Path

        fixture = Path(__file__).parent.parent.parent / "linkedin" / "operations" / "fixtures" / "profile_posts.json"
        with open(fixture) as f:
            raw = json.load(f)

        parser = ProfilePostsParser()
        result = parser.parse(raw)

        assert result.pagination.has_more is False


class TestParseProfilePostsWrapper:
    """Test parse_profile_posts compatibility wrapper."""

    def test_wrapper_returns_parse_result(self):
        """parse_profile_posts returns a ParseResult."""
        from linkedin.operations.parsers import parse_profile_posts, ParseResult

        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Wrapper test post",
                        "actorName": "Wrapper User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parse_profile_posts(raw)
        assert isinstance(result, ParseResult)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].text == "Wrapper test post"


# ======================
# Fix A1 — Pagination
# ======================

class TestProfilePostsParserPagination:
    """Test pagination extraction for profile posts."""

    def test_parse_with_pagination_metadata_has_more_true(self):
        """When metadata.paging.hasMore is True, has_more should be True."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Post with pagination",
                        "actorName": "Test User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ],
                "metadata": {
                    "paging": {
                        "hasMore": True,
                        "nextStart": 10
                    }
                }
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert result.pagination.has_more is True
        assert result.pagination.next_offset == 10

    def test_parse_with_pagination_metadata_has_more_false(self):
        """When metadata.paging.hasMore is False, has_more should be False."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [],
                "metadata": {
                    "paging": {
                        "hasMore": False
                    }
                }
            }
        }
        result = parser.parse(raw)
        assert result.pagination.has_more is False


# ======================
# Fix A2 — Schema
# ======================

class TestProfilePostsParserSchema:
    """Test ParsedProfilePost schema fields."""

    def test_parse_populates_author_urn_when_present(self):
        """author_urn is populated when post has author.urn field."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Post with author",
                        "actorName": "Test User",
                        "author": {
                            "urn": "urn:li:fsd_profile:ABC123"
                        },
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].author_urn == "urn:li:fsd_profile:ABC123"

    def test_parse_author_urn_none_when_missing(self):
        """author_urn is None when post lacks author field."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Post without author",
                        "actorName": "Test User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.data[0].author_urn is None

    def test_parse_populates_published_at_when_present(self):
        """published_at is populated when post has postedAt field."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Post with timestamp",
                        "actorName": "Test User",
                        "postedAt": "2024-01-15T10:30:00.000Z",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert result.data[0].published_at == "2024-01-15T10:30:00.000Z"

    def test_parse_published_at_none_when_missing(self):
        """published_at is None when post lacks timestamp field."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:activity:123",
                        "commentary": "Post without timestamp",
                        "actorName": "Test User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.data[0].published_at is None

    def test_parse_fixture_has_schema_fields(self):
        """Fixture parsing produces posts with author_urn and published_at attributes."""
        import json
        from pathlib import Path

        fixture = Path(__file__).parent.parent.parent / "linkedin" / "operations" / "fixtures" / "profile_posts.json"
        with open(fixture) as f:
            raw = json.load(f)

        parser = ProfilePostsParser()
        result = parser.parse(raw)

        for post in result.data:
            assert hasattr(post, "author_urn")
            assert hasattr(post, "published_at")


# ======================
# Fix A3 — URL parsing
# ======================

class TestProfilePostsParserUrl:
    """Test URL construction."""

    def test_constructed_url_parses_cleanly(self):
        """Constructed post URL parses cleanly without nested parens issues."""
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:123,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "URL test post",
                        "actorName": "Test User",
                        "type": "com.linkedin.voyager.dash.feed.Update"
                    }
                ]
            }
        }
        result = parser.parse(raw)
        url = result.data[0].url
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "www.linkedin.com"
        assert "/feed/update/" in parsed.path
        assert "((" not in url  # no double nested parens


# ======================
# Fix A4 — Registry literal
# ======================

class TestProfilePostsParserRegistry:
    """Test that ProfilePostsParser is in the literal PARSERS dict."""

    def test_parser_in_literal_registry(self):
        """fetchProfilePosts is in the literal PARSERS dict, not added via mutation."""
        assert "fetchProfilePosts" in PARSERS
        assert PARSERS["fetchProfilePosts"] is ProfilePostsParser
