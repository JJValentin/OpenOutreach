import pytest
from django.contrib import admin

from linkedin.models import WatchedSource, Signal
from linkedin.admin import WatchedSourceAdmin, SignalAdmin


def test_watched_source_is_registered():
    assert admin.site.is_registered(WatchedSource)


def test_signal_is_registered():
    assert admin.site.is_registered(Signal)


@pytest.mark.django_db
def test_admin_actions_update_source_state(db):
    from linkedin.models import Campaign
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    source = WatchedSource.objects.create(campaign=campaign, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me")
    source.consecutive_failures = 3
    source.last_error = "timeout"
    source.save()

    # Simulate reset_failure_count action
    WatchedSource.objects.filter(pk=source.pk).update(consecutive_failures=0, last_error="")
    source.refresh_from_db()
    assert source.consecutive_failures == 0
    assert source.last_error == ""
@pytest.mark.django_db
def test_status_filter_failing(db):
    """StatusFilter 'failing' shows only sources with consecutive_failures >= 1."""
    from linkedin.models import WatchedSource, Campaign
    from linkedin.admin import StatusFilter
    campaign = Campaign.objects.create(name="FilterTest", campaign_objective="test")
    failing = WatchedSource.objects.create(
        campaign=campaign, kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="failing-co", consecutive_failures=2, is_active=True,
    )
    healthy = WatchedSource.objects.create(
        campaign=campaign, kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="healthy-co", consecutive_failures=0, is_active=True,
    )

    class MockRequest:
        pass

    f = StatusFilter(MockRequest(), {"status": ["failing"]}, WatchedSource, None)
    result = f.queryset(MockRequest(), WatchedSource.objects.all())
    ids = list(result.values_list("id", flat=True))
    assert failing.id in ids
    assert healthy.id not in ids


@pytest.mark.django_db
def test_paused_status_method_when_paused(db):
    """paused_status returns 'Currently paused: yes' when paused_until is in the future."""
    from django.utils import timezone
    from datetime import timedelta
    from linkedin.models import SignalRadarState
    from linkedin.admin import SignalRadarStateAdmin

    state = SignalRadarState.objects.create(
        paused_until=timezone.now() + timedelta(hours=2)
    )
    admin_instance = SignalRadarStateAdmin(SignalRadarState, None)
    status = admin_instance.paused_status(state)
    assert "Currently paused: yes" in status


@pytest.mark.django_db
def test_paused_status_method_when_not_paused(db):
    """paused_status returns 'Currently paused: no' when not paused."""
    from linkedin.models import SignalRadarState
    from linkedin.admin import SignalRadarStateAdmin

    state = SignalRadarState.objects.create(paused_until=None)
    admin_instance = SignalRadarStateAdmin(SignalRadarState, None)
    status = admin_instance.paused_status(state)
    assert status == "Currently paused: no"


@pytest.mark.django_db
def test_last_successful_poll_at_in_list_display(db):
    """WatchedSourceAdmin must include last_successful_poll_at in list_display."""
    from linkedin.admin import WatchedSourceAdmin
    assert "last_successful_poll_at" in WatchedSourceAdmin.list_display