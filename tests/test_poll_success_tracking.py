"""
TDD test: poll_signals.py writes last_successful_poll_at on success.
"""
import pytest
from datetime import datetime, timezone as py_tz
from unittest.mock import MagicMock, patch, call
from django.utils import timezone


@pytest.mark.django_db
def test_handle_poll_watched_source_sets_last_successful_poll_at():
    """On successful poll, last_successful_poll_at is updated."""
    from linkedin.models import WatchedSource, Campaign, Task
    from linkedin.tasks.poll_signals import handle_poll_watched_source
    from linkedin.signals.polling import PollResult

    campaign = Campaign.objects.create(name="TrackingTest", campaign_objective="test")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="track-test",
        display_name="Track Test",
        is_active=True,
    )
    assert source.last_successful_poll_at is None

    task = Task.objects.create(
        task_type=Task.TaskType.POLL_WATCHED_SOURCE,
        scheduled_at=timezone.now(),
        payload={"watched_source_id": source.id},
    )

    mock_result = PollResult(posts_seen=5, engagers_seen=3, signals_created=1, signals_updated=0)

    with patch("linkedin.tasks.poll_signals.poll_watched_source", return_value=mock_result):
        with patch("linkedin.tasks.poll_signals._reschedule"):
            handle_poll_watched_source(task, session=MagicMock(), qualifiers={})

    source.refresh_from_db()
    assert source.last_successful_poll_at is not None
    assert source.consecutive_failures == 0


@pytest.mark.django_db
def test_handle_poll_watched_source_does_not_set_on_failure():
    """On failed poll, last_successful_poll_at is NOT updated."""
    from linkedin.models import WatchedSource, Campaign, Task
    from linkedin.tasks.poll_signals import handle_poll_watched_source

    campaign = Campaign.objects.create(name="FailTest", campaign_objective="test")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="fail-test",
        display_name="Fail Test",
        is_active=True,
    )

    task = Task.objects.create(
        task_type=Task.TaskType.POLL_WATCHED_SOURCE,
        scheduled_at=timezone.now(),
        payload={"watched_source_id": source.id},
    )

    with patch("linkedin.tasks.poll_signals.poll_watched_source", side_effect=Exception("network error")):
        with patch("linkedin.tasks.poll_signals._reschedule"):
            try:
                handle_poll_watched_source(task, session=MagicMock(), qualifiers={})
            except Exception:
                pass

    source.refresh_from_db()
    assert source.last_successful_poll_at is None
    assert source.consecutive_failures == 1