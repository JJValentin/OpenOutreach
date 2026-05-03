"""Unit tests for Signal Radar smoke test — C1 + C2 classification logic.

These tests cover:
  C1 — failure-type classification (RESPONSE_SHAPE_CHANGED/NEEDS_RECON -> FAIL; POST_UNAVAILABLE -> SKIP)
  C2 — zero-posts causes engagement ops to be SKIP; --fallback-post-urn exercises them

Tests run with real Django (pytest-django), but mock LinkedIn/Chrome infrastructure.
The functions under test (classify_failure, run_operation, compute_exit_code) are pure
and do not touch the database or Chrome.
"""
import pytest
from unittest.mock import MagicMock

from linkedin.operations.health import FailureType
from linkedin.operations.parsers import ParseResult, PaginationInfo

_PAGINATION_DONE = PaginationInfo(has_more=False)

# Import the module under test — it lives alongside this test file
# Adjust sys.path so we can import it even though it's not a standard Django app module
import sys
import os
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# smoke_test.py calls django.setup() at module level inside an os.environ.setdefault guard.
# Since pytest-django already ran django.setup() before us, this is safe to import.
import smoke_test as st

STATUS_PASS = st.STATUS_PASS
STATUS_FAIL = st.STATUS_FAIL
STATUS_SKIP = st.STATUS_SKIP
EXIT_OK = st.EXIT_OK
EXIT_FAIL = st.EXIT_FAIL


# ---------------------------------------------------------------------------
# C1 — classify_failure tests
# ---------------------------------------------------------------------------

class TestClassifyFailure:
    """C1: FailureType -> PASS/FAIL/SKIP classification."""

    def test_none_is_pass(self):
        assert st.classify_failure(None) == STATUS_PASS

    def test_response_shape_changed_treated_as_fail(self):
        """C1: RESPONSE_SHAPE_CHANGED must be FAIL (previously was treated as PASS)."""
        assert st.classify_failure(FailureType.RESPONSE_SHAPE_CHANGED) == STATUS_FAIL

    def test_needs_recon_treated_as_fail(self):
        """C1: NEEDS_RECON must be FAIL."""
        assert st.classify_failure(FailureType.NEEDS_RECON) == STATUS_FAIL

    def test_post_unavailable_treated_as_skip_not_pass(self):
        """C1: POST_UNAVAILABLE must be SKIP, not PASS."""
        result = st.classify_failure(FailureType.POST_UNAVAILABLE)
        assert result == STATUS_SKIP
        assert result != STATUS_PASS

    def test_auth_expired_is_fail(self):
        assert st.classify_failure(FailureType.AUTH_EXPIRED) == STATUS_FAIL

    def test_query_id_invalid_is_fail(self):
        assert st.classify_failure(FailureType.QUERY_ID_INVALID) == STATUS_FAIL

    def test_temporary_block_is_fail(self):
        assert st.classify_failure(FailureType.TEMPORARY_BLOCK) == STATUS_FAIL

    def test_partial_data_is_fail(self):
        assert st.classify_failure(FailureType.PARTIAL_DATA) == STATUS_FAIL


# ---------------------------------------------------------------------------
# C1 — run_operation tests
# ---------------------------------------------------------------------------

class TestRunOperation:
    """run_operation wraps an executor call and returns classified OpResult."""

    def _make_executor(self):
        return MagicMock()

    def test_successful_result_is_pass(self):
        executor = self._make_executor()
        item = MagicMock()
        fn = MagicMock(return_value=ParseResult(data=[item], pagination=_PAGINATION_DONE, failure=None))
        result = st.run_operation(executor, "testOp", fn)
        assert result.status == STATUS_PASS
        assert result.count == 1

    def test_response_shape_changed_result_is_fail(self):
        executor = self._make_executor()
        fn = MagicMock(return_value=ParseResult(
            data=[], pagination=_PAGINATION_DONE,
            failure=FailureType.RESPONSE_SHAPE_CHANGED, failure_message="shape changed"
        ))
        result = st.run_operation(executor, "testOp", fn)
        assert result.status == STATUS_FAIL
        assert "shape changed" in (result.error or "")

    def test_needs_recon_result_is_fail(self):
        executor = self._make_executor()
        fn = MagicMock(return_value=ParseResult(
            data=[], pagination=_PAGINATION_DONE, failure=FailureType.NEEDS_RECON
        ))
        result = st.run_operation(executor, "testOp", fn)
        assert result.status == STATUS_FAIL

    def test_post_unavailable_result_is_skip(self):
        executor = self._make_executor()
        fn = MagicMock(return_value=ParseResult(
            data=[], pagination=_PAGINATION_DONE, failure=FailureType.POST_UNAVAILABLE
        ))
        result = st.run_operation(executor, "testOp", fn)
        assert result.status == STATUS_SKIP

    def test_exception_is_fail(self):
        executor = self._make_executor()
        fn = MagicMock(side_effect=RuntimeError("network error"))
        result = st.run_operation(executor, "testOp", fn)
        assert result.status == STATUS_FAIL
        assert "network error" in result.error


# ---------------------------------------------------------------------------
# C1 — compute_exit_code tests
# ---------------------------------------------------------------------------

class TestComputeExitCode:
    """C1: Exit code logic."""

    def _r(self, name, status):
        return st.OpResult(name=name, status=status)

    def test_all_pass_exits_0(self):
        results = [self._r("op1", STATUS_PASS), self._r("op2", STATUS_PASS)]
        assert st.compute_exit_code(results) == EXIT_OK

    def test_any_fail_exits_1(self):
        results = [self._r("op1", STATUS_PASS), self._r("op2", STATUS_FAIL)]
        assert st.compute_exit_code(results) == EXIT_FAIL

    def test_all_skip_returns_exit_1(self):
        """C1: all-SKIP is insufficient health signal -> exit 1."""
        results = [self._r("op1", STATUS_SKIP), self._r("op2", STATUS_SKIP)]
        assert st.compute_exit_code(results) == EXIT_FAIL

    def test_pass_and_skip_no_fail_exits_0(self):
        results = [self._r("op1", STATUS_PASS), self._r("op2", STATUS_SKIP)]
        assert st.compute_exit_code(results) == EXIT_OK

    def test_fail_and_skip_exits_1(self):
        results = [self._r("op1", STATUS_FAIL), self._r("op2", STATUS_SKIP)]
        assert st.compute_exit_code(results) == EXIT_FAIL


# ---------------------------------------------------------------------------
# C2 — zero-posts engagement skip logic tests
# ---------------------------------------------------------------------------

class TestSkipsEngagementOpsWhenNoPosts:
    """C2: When fetchCompanyPosts returns 0 posts, engagement ops are SKIP not PASS."""

    def _run_engagement_section(self, executor, fallback_post_urn=None, first_post_urn=None):
        """Replicate the engagement classification logic from main() after fetchCompanyPosts."""
        engagement_urn = first_post_urn or fallback_post_urn

        results = []
        for op_name, fn_name in [
            ("fetchPostReactions", "fetch_post_reactions"),
            ("fetchPostComments", "fetch_post_comments"),
            ("fetchPostReposts", "fetch_post_reposts"),
        ]:
            if engagement_urn:
                fn = getattr(executor, fn_name)
                result = st.run_operation(executor, op_name, fn, engagement_urn, count=5)
            else:
                result = st.OpResult(name=op_name, status=STATUS_SKIP, count=0,
                                     error="No posts available; skipped")
            results.append(result)
        return results

    def test_skips_engagement_ops_when_no_posts(self):
        """C2: Without fallback URN and zero posts, all three engagement ops are SKIP."""
        executor = MagicMock()
        results = self._run_engagement_section(executor, fallback_post_urn=None, first_post_urn=None)

        for r in results:
            assert r.status == STATUS_SKIP, f"{r.name} should be SKIP but was {r.status}"

        # Confirm engagement methods were never called
        executor.fetch_post_reactions.assert_not_called()
        executor.fetch_post_comments.assert_not_called()
        executor.fetch_post_reposts.assert_not_called()

    def test_fallback_post_urn_exercises_engagement_ops(self):
        """C2: With --fallback-post-urn, engagement ops are called and can PASS."""
        executor = MagicMock()
        executor.fetch_post_reactions.return_value = ParseResult(data=[], pagination=_PAGINATION_DONE, failure=None)
        executor.fetch_post_comments.return_value = ParseResult(data=[], pagination=_PAGINATION_DONE, failure=None)
        executor.fetch_post_reposts.return_value = ParseResult(data=[], pagination=_PAGINATION_DONE, failure=None)

        results = self._run_engagement_section(executor, fallback_post_urn="urn:li:activity:42", first_post_urn=None)

        for r in results:
            assert r.status == STATUS_PASS, f"{r.name} should be PASS but was {r.status}"

        executor.fetch_post_reactions.assert_called_once()
        executor.fetch_post_comments.assert_called_once()
        executor.fetch_post_reposts.assert_called_once()

    def test_all_skip_from_zero_posts_gives_exit_1(self):
        """C2 + C1: all 4 ops SKIP -> exit 1 (no pass, no fail = insufficient signal)."""
        all_results = [
            st.OpResult(name="fetchCompanyPosts", status=STATUS_SKIP, count=0),
            st.OpResult(name="fetchPostReactions", status=STATUS_SKIP, count=0),
            st.OpResult(name="fetchPostComments", status=STATUS_SKIP, count=0),
            st.OpResult(name="fetchPostReposts", status=STATUS_SKIP, count=0),
        ]
        assert st.compute_exit_code(all_results) == EXIT_FAIL

    def test_posts_pass_with_engagement_skip_gives_exit_0(self):
        """C2 + C1: fetchCompanyPosts PASS (0 items) + engagement SKIP -> exit 0.
        Rationale: fetchCompanyPosts succeeded (PASS), providing a valid health signal.
        The spec says exit 0 when no FAIL and at least one PASS.
        """
        all_results = [
            st.OpResult(name="fetchCompanyPosts", status=STATUS_PASS, count=0),
            st.OpResult(name="fetchPostReactions", status=STATUS_SKIP, count=0),
            st.OpResult(name="fetchPostComments", status=STATUS_SKIP, count=0),
            st.OpResult(name="fetchPostReposts", status=STATUS_SKIP, count=0),
        ]
        # fetchCompanyPosts PASS is sufficient health signal -> exit 0
        assert st.compute_exit_code(all_results) == EXIT_OK


# ---------------------------------------------------------------------------
# OpResult property tests (C1: status field must be "PASS"/"FAIL"/"SKIP")
# ---------------------------------------------------------------------------

class TestOpResultProperties:
    """OpResult has correct passed/skipped/failed boolean properties."""

    def test_pass_properties(self):
        r = st.OpResult(name="op", status=STATUS_PASS)
        assert r.passed is True
        assert r.skipped is False
        assert r.failed is False

    def test_fail_properties(self):
        r = st.OpResult(name="op", status=STATUS_FAIL)
        assert r.passed is False
        assert r.skipped is False
        assert r.failed is True

    def test_skip_properties(self):
        r = st.OpResult(name="op", status=STATUS_SKIP)
        assert r.passed is False
        assert r.skipped is True
        assert r.failed is False
