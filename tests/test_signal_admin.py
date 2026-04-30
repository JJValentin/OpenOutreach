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
