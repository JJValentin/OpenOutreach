"""Tests for LinkedIn operation parsers."""

import pytest
from linkedin.operations.parsers import (
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
