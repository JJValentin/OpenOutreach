"""Unit tests for Signal Radar smoke test script.

Verifies smoke_test.py exit codes for:
1. All operations succeed -> exit 0
2. One operation fails -> exit 1
3. Chrome/CDP connection fails -> exit 2
4. Zero results -> exit 0 (valid, not a failure)
5. Operation raises exception -> exit 1
6. JSON output structure is correct
"""
import pytest
from unittest.mock import MagicMock, patch

from linkedin.operations.health import FailureType
from linkedin.operations.parsers import ParseResult, PaginationInfo, ParsedPost
from linkedin.operations.executor import LinkedInOperationExecutor


def make_parsed_post(urn="urn:li:activity:1"):
    """Create a minimal ParsedPost for testing."""
    return ParsedPost(
        urn=urn,
        author_urn="urn:li:member:1",
        text="Test post",
        published_at=None,
        reaction_count=0,
        comment_count=0,
        repost_count=0,
        url=None,
    )


def make_success_result(with_posts=True):
    """Create a ParseResult indicating success."""
    data = [make_parsed_post()] if with_posts else []
    return ParseResult(
        data=data,
        pagination=PaginationInfo(has_more=False, total=len(data)),
        failure=None,
        failure_message="",
    )


def make_failure_result(failure_type=FailureType.AUTH_EXPIRED):
    """Create a ParseResult indicating failure."""
    return ParseResult(
        data=[],
        pagination=PaginationInfo(has_more=False, total=0),
        failure=failure_type,
        failure_message=str(failure_type),
    )


@pytest.fixture
def active_profile(db):
    """Create an active LinkedInProfile for tests."""
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


def _patch_playwright(mock_pw):
    """Set up minimal Playwright mock."""
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_browser.contexts = [mock_context]
    mock_context.pages = [mock_page]
    mock_pw.return_value.__enter__ = MagicMock(return_value=mock_pw.return_value)
    mock_pw.return_value.__exit__ = MagicMock(return_value=False)
    mock_pw.return_value.chromium.connect_over_cdp.return_value = mock_browser
    return mock_browser, mock_context, mock_page


class TestSmokeTestExitCodes:
    """Test smoke_test.py exit codes under various conditions."""

    def test_all_ops_succeed_exit_0(self, db, active_profile):
        """Mock executor returning success for all 4 ops -> exit 0."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=True)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False)
                    assert exit_code == 0, f"Expected exit 0 when all ops succeed, got {exit_code}"

    def test_one_op_fails_exit_1(self, db, active_profile):
        """Mock executor returning failure for one op -> exit 1."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=True)
        mock_executor.fetch_post_reactions.return_value = make_failure_result(FailureType.AUTH_EXPIRED)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False)
                    assert exit_code == 1, f"Expected exit 1 when at least one op fails, got {exit_code}"

    def test_chrome_connection_fails_exit_2(self, db, active_profile):
        """Mock Chrome CDP connection fails -> exit 2."""
        with patch("linkedin.scripts.smoke_test.get_chrome_ws_url", return_value=None):
            from linkedin.scripts.smoke_test import main
            exit_code = main(target_company="1337", json_output=False)
            assert exit_code == 2, f"Expected exit 2 when Chrome CDP fails, got {exit_code}"

    def test_zero_results_is_pass_exit_0(self, db, active_profile):
        """Zero results returned -> PASS (exit 0), not fail."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        # Returns empty data - no posts, so reactions/comments/reposts are skipped (PASS)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False)
                    assert exit_code == 0, f"Expected exit 0 for zero results, got {exit_code}"

    def test_operation_exception_exit_1(self, db, active_profile):
        """Exception during operation -> exit 1."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=True)
        mock_executor.fetch_post_reactions.side_effect = Exception("Network error")
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False)
                    assert exit_code == 1, f"Expected exit 1 when operation raises, got {exit_code}"


class TestSmokeTestJSONOutput:
    """Test --json output format."""

    def test_json_output_structure(self, db, active_profile):
        """JSON output has required fields: overall_pass, exit_code, operations, profile."""
        import json
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    printed_lines = []
                    with patch("builtins.print", side_effect=lambda *a, **kw: printed_lines.append(str(a[0]) if a else "")):
                        from linkedin.scripts.smoke_test import main
                        exit_code = main(target_company="1337", json_output=True)

                    # Find the JSON line
                    json_line = None
                    for line in printed_lines:
                        try:
                            obj = json.loads(line)
                            json_line = obj
                            break
                        except (json.JSONDecodeError, TypeError):
                            continue

                    assert json_line is not None, f"No valid JSON in output: {printed_lines}"
                    assert "overall_pass" in json_line, "Missing 'overall_pass'"
                    assert "exit_code" in json_line, "Missing 'exit_code'"
                    assert "operations" in json_line, "Missing 'operations'"
                    assert "profile" in json_line, "Missing 'profile'"
                    # Check all 4 operations are present
                    ops = json_line["operations"]
                    for op_name in ("fetchCompanyPosts", "fetchPostReactions", "fetchPostComments", "fetchPostReposts"):
                        assert op_name in ops, f"Missing operation '{op_name}' in JSON"
                        assert "pass" in ops[op_name], f"Missing 'pass' for {op_name}"
                        assert "count" in ops[op_name], f"Missing 'count' for {op_name}"

    def test_include_profile_posts_flag_pass(self, db, active_profile):
        """With --include-profile-posts and all ops succeed including fetchProfilePosts -> exit 0."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=True)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_profile_posts.return_value = make_success_result(with_posts=True)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False, include_profile_posts="joshuajvalentin")
                    assert exit_code == 0, f"Expected exit 0, got {exit_code}"
                    mock_executor.fetch_profile_posts.assert_called_once_with("joshuajvalentin")

    def test_include_profile_posts_network_error(self, db, active_profile):
        """With --include-profile-posts, fetchProfilePosts fails with NETWORK_ERROR -> exit 1."""
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=True)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_profile_posts.return_value = make_failure_result(FailureType.NETWORK_ERROR)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    from linkedin.scripts.smoke_test import main
                    exit_code = main(target_company="1337", json_output=False, include_profile_posts="joshuajvalentin")
                    assert exit_code == 1, f"Expected exit 1 for NETWORK_ERROR, got {exit_code}"

    def test_json_includes_profile_posts_when_flag_set(self, db, active_profile):
        """JSON output includes fetchProfilePosts when --include-profile-posts is set."""
        import json
        mock_executor = MagicMock(spec=LinkedInOperationExecutor)
        mock_executor.fetch_company_posts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reactions.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_comments.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_post_reposts.return_value = make_success_result(with_posts=False)
        mock_executor.fetch_profile_posts.return_value = make_success_result(with_posts=True)

        with patch("linkedin.scripts.smoke_test.LinkedInOperationExecutor", return_value=mock_executor):
            with patch("linkedin.scripts.smoke_test.get_chrome_ws_url",
                       return_value="ws://localhost:9222/devtools/browser/test"):
                with patch("linkedin.scripts.smoke_test.sync_playwright") as mock_pw:
                    _patch_playwright(mock_pw)
                    printed_lines = []
                    with patch("builtins.print", side_effect=lambda *a, **kw: printed_lines.append(str(a[0]) if a else "")):
                        from linkedin.scripts.smoke_test import main
                        exit_code = main(target_company="1337", json_output=True, include_profile_posts="joshuajvalentin")

                    json_line = None
                    for line in printed_lines:
                        try:
                            obj = json.loads(line)
                            json_line = obj
                            break
                        except (json.JSONDecodeError, TypeError):
                            continue

                    assert json_line is not None, f"No valid JSON in output: {printed_lines}"
                    ops = json_line["operations"]
                    assert "fetchProfilePosts" in ops, f"Missing fetchProfilePosts in operations: {list(ops.keys())}"
                    assert ops["fetchProfilePosts"]["status"] == "PASS", f"Expected PASS, got {ops['fetchProfilePosts']['status']}"
