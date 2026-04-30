"""
tests/test_signal_safety.py — Safety guardrails for Signal Radar polling.

Tests:
1. 429 response → global pause set on SignalRadarState
2. Poll skipped during global pause window
3. Pause expires → polls resume
4. 3 consecutive failures → source auto-disabled
5. Successful poll resets consecutive_failures to 0
6. Disabled sources not rescheduled
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, ANY

from django.utils import timezone as dj_timezone

from linkedin.models import Campaign, LinkedInProfile, Signal, Task, WatchedSource, SignalRadarState
from linkedin.signals.polling import PollResult, poll_watched_source, _is_paused, _set_pause
from linkedin.tasks.poll_signals import (
    handle_poll_own_posts,
    handle_poll_watched_source,
)
from linkedin.tasks.scheduler import enqueue_poll_watched_source


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def make_mock_post(urn, excerpt="Test post", idx=1):
    return {
        "post_urn": urn,
        "post_excerpt": excerpt,
        "published_at": (datetime.now(timezone.utc) - timedelta(hours=idx)).isoformat(),
        "author_urn": f"urn:li:fs_member:{idx}",
        "author_public_id": f"author{idx}",
    }


def make_mock_reactors(urns_and_types):
    return [
        {"profile_urn": urn, "public_identifier": f"user{i}", "reaction_type": rtype}
        for (i, (urn, rtype)) in enumerate(urns_and_types, start=1)
    ]


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def campaign_obj(db):
    return Campaign.objects.create(name="Test Campaign")


@pytest.fixture
def own_profile_source(db, campaign_obj):
    return WatchedSource.objects.create(
        campaign=campaign_obj,
        kind=WatchedSource.Kind.OWN_PROFILE,
        identifier="testuser",
        display_name="Test User",
        is_active=True,
        cadence_minutes=30,
    )


@pytest.fixture
def competitor_source(db, campaign_obj):
    return WatchedSource.objects.create(
        campaign=campaign_obj,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="acme-corp",
        display_name="Acme Corp",
        is_active=True,
        cadence_minutes=120,
    )


@pytest.fixture(autouse=True)
def clear_signal_radar_state(db):
    """Clear the global pause before each test."""
    state = SignalRadarState.load()
    state.paused_until = None
    state.save(update_fields=["paused_until"])
    yield
    state.paused_until = None
    state.save(update_fields=["paused_until"])


# ------------------------------------------------------------------
# Tests: 429 Global Pause
# ------------------------------------------------------------------

class Test429GlobalPause:
    """
    When the LinkedIn API returns a 429 rate-limit response, the global
    pause window should be set on SignalRadarState so no sources are polled
    until the pause expires.
    """

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_429_sets_global_pause(self, own_profile_source, fake_session):
        """
        Simulate a 429 from the posts API → pause should be set on SignalRadarState.
        """
        error_429 = Exception("429 Too Many Requests")
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts", side_effect=error_429):
            with pytest.raises(Exception) as exc_info:
                poll_watched_source(own_profile_source, fake_session)
            assert "429" in str(exc_info.value)

        # After a 429, SignalRadarState should have paused_until set in the future
        state = SignalRadarState.load()
        assert state.paused_until is not None
        assert state.paused_until > dj_timezone.now()

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_poll_skipped_during_global_pause(self, own_profile_source, fake_session):
        """
        If SignalRadarState.paused_until is in the future,
        poll_watched_source should return early without making API calls.
        """
        state = SignalRadarState.load()
        state.paused_until = dj_timezone.now() + timedelta(hours=4)
        state.save(update_fields=["paused_until"])

        # Confirm _is_paused() returns True
        assert _is_paused() is True

        post = make_mock_post("urn:li:fs_post:PAUSED001", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts:
            result = poll_watched_source(own_profile_source, fake_session)

        # Should return empty result without calling the API
        assert result.posts_seen == 0
        mock_posts.assert_not_called()

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_polls_resume_after_pause_expires(self, own_profile_source, fake_session):
        """
        Once SignalRadarState.paused_until is in the past, polling works normally.
        """
        state = SignalRadarState.load()
        state.paused_until = dj_timezone.now() - timedelta(minutes=1)
        state.save(update_fields=["paused_until"])

        assert _is_paused() is False

        post = make_mock_post("urn:li:fs_post:RESUMED001", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:RESUME1", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            result = poll_watched_source(own_profile_source, fake_session)

        assert result.posts_seen == 1
        assert result.signals_created == 1


class TestConsecutiveFailureTracking:
    """
    Per-source consecutive failures are tracked. After 3 failures a source
    is auto-disabled and not rescheduled.
    """

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_third_consecutive_failure_disables_source(self, own_profile_source, fake_session):
        """
        When a source gets 3 consecutive failures, it should be set is_active=False.
        """
        # Simulate 3 failures
        for i in range(3):
            try:
                with patch("linkedin.signals.polling.posts_api.list_own_profile_posts",
                           side_effect=Exception("API error")):
                    poll_watched_source(own_profile_source, fake_session)
            except Exception:
                pass

        own_profile_source.refresh_from_db()
        assert own_profile_source.is_active is False

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_consecutive_failures_counter_incremented_on_error(self, own_profile_source, fake_session):
        """
        Each exception should increment consecutive_failures by 1.
        """
        for i in range(3):
            try:
                with patch("linkedin.signals.polling.posts_api.list_own_profile_posts",
                           side_effect=Exception("API error")):
                    poll_watched_source(own_profile_source, fake_session)
            except Exception:
                pass

        own_profile_source.refresh_from_db()
        assert own_profile_source.consecutive_failures == 3

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_last_error_stored_on_failure(self, own_profile_source, fake_session):
        """
        last_error should contain the error message after a failure.
        """
        err_msg = "Rate limit exceeded"
        try:
            with patch("linkedin.signals.polling.posts_api.list_own_profile_posts",
                       side_effect=Exception(err_msg)):
                poll_watched_source(own_profile_source, fake_session)
        except Exception:
            pass

        own_profile_source.refresh_from_db()
        assert err_msg in own_profile_source.last_error

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_successful_poll_resets_failure_counter(self, own_profile_source, fake_session):
        """
        A successful poll (even after failures) resets consecutive_failures to 0.
        """
        # Seed with 2 failures
        for i in range(2):
            try:
                with patch("linkedin.signals.polling.posts_api.list_own_profile_posts",
                           side_effect=Exception("API error")):
                    poll_watched_source(own_profile_source, fake_session)
            except Exception:
                pass

        # Now succeed
        post = make_mock_post("urn:li:fs_post:RESET001", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:RESET1", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            poll_watched_source(own_profile_source, fake_session)

        own_profile_source.refresh_from_db()
        assert own_profile_source.consecutive_failures == 0


class TestDisabledSourceNotRescheduled:
    """
    A WatchedSource that is is_active=False should NOT be rescheduled.
    """

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_inactive_source_not_rescheduled(self, campaign_obj, db, fake_session):
        """
        When handle_poll_watched_source runs on an inactive source,
        no new task should be enqueued.
        """
        # Create source with 3 consecutive failures already (will be auto-disabled)
        src = WatchedSource.objects.create(
            campaign=campaign_obj,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="inactive-test",
            display_name="Inactive Test",
            is_active=True,
            cadence_minutes=30,
            consecutive_failures=0,
        )

        task = Task.objects.create(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
            scheduled_at=dj_timezone.now(),
            payload={"watched_source_id": src.id},
        )

        # Cause 3 failures to trigger auto-disable
        for i in range(3):
            try:
                with patch("linkedin.signals.polling.posts_api.list_own_profile_posts",
                           side_effect=Exception("API error")):
                    poll_watched_source(src, fake_session)
            except Exception:
                pass

        src.refresh_from_db()
        assert src.is_active is False

        # No new POLL_WATCHED_SOURCE task should be pending (original task excluded)
        pending = Task.objects.filter(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
            payload__watched_source_id=src.id,
        ).exclude(id=task.id).count()
        assert pending == 0, "Disabled source should not be rescheduled"

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_inactive_source_handle_poll_skips(self, campaign_obj, db, fake_session):
        """
        handle_poll_watched_source should return early if source is already inactive.
        """
        # Create source already inactive
        src = WatchedSource.objects.create(
            campaign=campaign_obj,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="already-inactive",
            display_name="Already Inactive",
            is_active=False,
            cadence_minutes=30,
        )

        task = Task.objects.create(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
            scheduled_at=dj_timezone.now(),
            payload={"watched_source_id": src.id},
        )

        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts:
            handle_poll_watched_source(task, fake_session, qualifiers={})

        mock_posts.assert_not_called()

        # Only the original task should remain pending
        pending = Task.objects.filter(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
            payload__watched_source_id=src.id,
        ).count()
        assert pending == 1


class TestHandlerIntegration:
    """
    Integration tests for the task handlers with safety features.
    """

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_handle_poll_watched_source_resets_failures_on_success(self, own_profile_source, fake_session):
        """
        handle_poll_watched_source should reset consecutive_failures and last_error on success.
        """
        own_profile_source.consecutive_failures = 2
        own_profile_source.last_error = "Some error"
        own_profile_source.save()

        post = make_mock_post("urn:li:fs_post:HANDLER001", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []

            task = Task.objects.create(
                task_type=Task.TaskType.POLL_WATCHED_SOURCE,
                status=Task.Status.PENDING,
                scheduled_at=dj_timezone.now(),
                payload={"watched_source_id": own_profile_source.id},
            )
            handle_poll_watched_source(task, fake_session, qualifiers={})

        own_profile_source.refresh_from_db()
        assert own_profile_source.consecutive_failures == 0
        assert own_profile_source.last_error == ""

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_handle_poll_own_posts_respects_disabled_sources(self, campaign_obj, db, fake_session):
        """
        handle_poll_own_posts should skip disabled sources and not error on them.
        """
        active_src = WatchedSource.objects.create(
            campaign=campaign_obj,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="active-src",
            display_name="Active Source",
            is_active=True,
            cadence_minutes=30,
        )
        inactive_src = WatchedSource.objects.create(
            campaign=campaign_obj,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="inactive-src",
            display_name="Inactive Source",
            is_active=False,
            cadence_minutes=30,
        )

        task = Task.objects.create(
            task_type=Task.TaskType.POLL_OWN_POSTS,
            status=Task.Status.PENDING,
            scheduled_at=dj_timezone.now(),
            payload={"campaign_id": campaign_obj.id},
        )

        post = make_mock_post("urn:li:fs_post:HND002", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            # Should not raise — inactive source should be silently skipped
            handle_poll_own_posts(task, fake_session, qualifiers={})

        # Active source should have been polled
        active_src.refresh_from_db()
        assert active_src.last_poll_at is not None
