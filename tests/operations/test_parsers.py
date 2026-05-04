"""Tests for LinkedIn operation parsers."""

import pytest
from linkedin.operations.parsers import (
    ProfilePostsParser,
    CompanyPostsParser,
    CommentsParser,
    ReactionsParser,
    RepostsParser,
    get_parser,
)
from linkedin.operations.health import FailureType


class TestCompanyPostsParser:
    def test_parse_empty_response(self):
        parser = CompanyPostsParser()
        result = parser.parse({"data": {"organizationPageUpdateV2": None}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED
        assert len(result.data) == 0

    def test_parse_shape_changed(self):
        parser = CompanyPostsParser()
        result = parser.parse({"data": {"unexpected_key": []}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED

    def test_parse_valid_structure(self):
        parser = CompanyPostsParser()
        raw = {
            "data": {
                "organizationPageUpdateV2": {
                    "paging": {"count": 10, "start": 0, "total": 42},
                    "*elements": [],
                    "$type": "com.linkedin.restli.common.CollectionResponse"
                }
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert result.pagination.total == 42


class TestCommentsParser:
    def test_parse_empty_response(self):
        parser = CommentsParser()
        result = parser.parse({"data": {"socialDashCommentsBySocialDetail": None}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED

    def test_parse_shape_changed(self):
        parser = CommentsParser()
        result = parser.parse({"data": {"wrong_key": {}}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED


class TestReactionsParser:
    def test_parse_empty_response(self):
        parser = ReactionsParser()
        result = parser.parse({"data": {"socialDashReactionsByThreadUrn": None}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED

    def test_parse_shape_changed(self):
        parser = ReactionsParser()
        result = parser.parse({"data": {"wrong_key": {}}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED


class TestRepostsParser:
    def test_parse_empty_response(self):
        parser = RepostsParser()
        result = parser.parse({"data": {"reshareFeedByTargetUrn": None}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED


class TestGetParser:
    def test_all_captured_operations_have_parsers(self):
        from linkedin.operations.registry import REGISTRY
        for name, op in REGISTRY.items():
            if "CAPTURED" in op.status:
                parser = get_parser(name)
                assert parser is not None
class TestProfilePostsParser:
    def test_parse_empty_response(self):
        parser = ProfilePostsParser()
        result = parser.parse({"data": {"posts": None}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED
        assert len(result.data) == 0

    def test_parse_shape_changed(self):
        parser = ProfilePostsParser()
        result = parser.parse({"data": {"wrong_key": {}}})
        assert result.failure == FailureType.RESPONSE_SHAPE_CHANGED

    def test_parse_valid_posts(self):
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7457085421605539840,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Hello world",
                        "actorName": "Tyler Denk",
                        "type": "com.linkedin.voyager.dash.feed.Update",
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        post = result.data[0]
        assert post.urn == "urn:li:fsd_update:(urn:li:activity:7457085421605539840,MAIN_FEED,DEBUG_REASON,DEFAULT,false)"
        assert post.author_name == "Tyler Denk"
        assert post.text == "Hello world"
        assert post.content_type == "com.linkedin.voyager.dash.feed.Update"
        assert post.url is not None
        assert "7457085421605539840" in post.url

    def test_parse_empty_posts_list(self):
        parser = ProfilePostsParser()
        raw = {"data": {"posts": []}}
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 0

    def test_parse_with_media_content(self):
        parser = ProfilePostsParser()
        raw = {
            "data": {
                "posts": [
                    {
                        "entityUrn": "urn:li:fsd_update:(urn:li:activity:7457075685585555456,MAIN_FEED,DEBUG_REASON,DEFAULT,false)",
                        "commentary": "Post with image",
                        "actorName": "Tyler Denk",
                        "type": "com.linkedin.voyager.dash.feed.Update",
                    }
                ]
            }
        }
        result = parser.parse(raw)
        assert result.failure is None
        assert len(result.data) == 1
        assert result.data[0].text == "Post with image"