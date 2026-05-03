"""RED phase: smoke test for Signal Radar operations.

Verifies smoke_test.py exit codes for:
1. All operations succeed -> exit 0
2. One operation fails -> exit 1
3. Chrome/CDP connection fails -> exit 2
4. Zero results -> exit 0 (valid, not a failure)
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Optional

from linkedin.operations.health import FailureType
from linkedin.operations.parsers import ParseResult, PaginationInfo


@dataclass
class MockParseResult:
    failure: Optional[FailureType] = None
    failure_message: str = ""
    data: list = None
    pagination: Optional[PaginationInfo] = None

    def __post_init__(self):
        if self.data is None:
            self.data = []
        if self.pagination is None:
            self.pagination = PaginationInfo(has_more=False)


class MockExecutor:
    def __init__(self, results: dict):
        self._results = results

    def fetch_company_posts(self, company_urn_or_slug, start=0, count=5):
        return self._get_result("fetchCompanyPosts")

    def fetch_post_reactions(self, post_urn, start=0, count=5):
        return self._get_result("fetchPostReactions")

    def fetch_post_comments(self, post_urn, start=0, count=5):
        return self._get_result("fetchPostComments")

    def fetch_post_reposts(self, post_urn, start=0, count=5):
        return self._get_result("fetchPostReposts")

    def _get_result(self, op_name):
        val = self._results.get(op_name)
        if isinstance(val, Exception):
            raise val
        return val


@pytest.fixture
def mock_cdp_ok():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/test"
        }
        mock_get.return_value = mock_response
        yield mock_get


@pytest.fixture
def active_profile(db):
    from linkedin.models import LinkedInProfile
    from tests.factories import UserFactory
    user = UserFactory(username="testuser_scenario")
    profile = LinkedInProfile.objects.create(
        user=user,
        linkedin_username="testuser",
        linkedin_password="testpass",
        active=True,
    )
    yield profile
    profile.delete()
    user.delete()


class TestSmokeTestExitCodes:
    def test_all_ops_succeed_exit_0(self, db, mock_cdp_ok, active_profile):
        success_result = MockParseResult(
            failure=None,
            data=[{"post_urn": "urn:li:activity:1"}],
            pagination=PaginationInfo(has_more=False, total=1),
        )
        mock_executor = MockExecutor({
            "fetchCompanyPosts": success_result,
            "fetchPostReactions": success_result,
            "fetchPostComments": success_result,
            "fetchPostReposts": success_result,
        })
        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.PlaywrightLinkedinAPI"):
                from linkedin.scripts.smoke_test import main
                exit_code = main(target_company="1337", json_output=False)
                assert exit_code == 0, "Expected exit 0 when all operations succeed"

    def test_one_op_fails_exit_1(self, db, mock_cdp_ok, active_profile):
        success_result = MockParseResult(
            failure=None,
            data=[{"post_urn": "urn:li:activity:1"}],
            pagination=PaginationInfo(has_more=False, total=1),
        )
        failure_result = MockParseResult(
            failure=FailureType.AUTH_EXPIRED,
            failure_message="Auth expired",
            data=[],
        )
        mock_executor = MockExecutor({
            "fetchCompanyPosts": success_result,
            "fetchPostReactions": failure_result,
            "fetchPostComments": success_result,
            "fetchPostReposts": success_result,
        })
        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.PlaywrightLinkedinAPI"):
                from linkedin.scripts.smoke_test import main
                exit_code = main(target_company="1337", json_output=False)
                assert exit_code == 1, "Expected exit 1 when at least one operation fails"

    def test_chrome_connection_fails_exit_2(self, db, active_profile):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            from linkedin.scripts.smoke_test import main
            exit_code = main(target_company="1337", json_output=False)
            assert exit_code == 2, "Expected exit 2 when Chrome CDP connection fails"

    def test_zero_results_is_pass_exit_0(self, db, mock_cdp_ok, active_profile):
        zero_result = MockParseResult(
            failure=None,
            data=[],
            pagination=PaginationInfo(has_more=False),
        )
        mock_executor = MockExecutor({
            "fetchCompanyPosts": zero_result,
            "fetchPostReactions": zero_result,
            "fetchPostComments": zero_result,
            "fetchPostReposts": zero_result,
        })
        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.PlaywrightLinkedinAPI"):
                from linkedin.scripts.smoke_test import main
                exit_code = main(target_company="1337", json_output=False)
                assert exit_code == 0, "Expected exit 0 when zero results returned (valid)"

    def test_operation_exception_exit_1(self, db, mock_cdp_ok, active_profile):
        success_result = MockParseResult(
            failure=None,
            data=[{"post_urn": "urn:li:activity:1"}],
        )
        mock_executor = MockExecutor({
            "fetchCompanyPosts": success_result,
            "fetchPostReactions": Exception("Network error"),
            "fetchPostComments": success_result,
            "fetchPostReposts": success_result,
        })
        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.PlaywrightLinkedinAPI"):
                from linkedin.scripts.smoke_test import main
                exit_code = main(target_company="1337", json_output=False)
                assert exit_code == 1, "Expected exit 1 when operation raises exception"


class TestSmokeTestJSONOutput:
    def test_json_output_structure(self, db, mock_cdp_ok, active_profile):
        success_result = MockParseResult(
            failure=None,
            data=[{"post_urn": "urn:li:activity:1"}],
            pagination=PaginationInfo(has_more=False, total=1),
        )
        mock_executor = MockExecutor({
            "fetchCompanyPosts": success_result,
            "fetchPostReactions": success_result,
            "fetchPostComments": success_result,
            "fetchPostReposts": success_result,
        })
        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.PlaywrightLinkedinAPI"):
                from linkedin.scripts.smoke_test import main
                with patch("builtins.print") as mock_print:
                    main(target_company="1337", json_output=True)
                    call_args = [str(c) for c in mock_print.call_args_list]
                    json_str = [a for a in call_args if "overall_pass" in a or "exit_code" in a]
                    assert json_str, f"Expected JSON to be printed, got: {call_args}"