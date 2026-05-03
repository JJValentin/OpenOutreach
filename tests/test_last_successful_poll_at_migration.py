"""
TDD test: WatchedSource.last_successful_poll_at field.
Write BEFORE the field is added to the model — test should fail RED first.
"""
import pytest
from django.utils import timezone


@pytest.mark.django_db
def test_last_successful_poll_at_field_exists():
    """last_successful_poll_at field must exist on WatchedSource model."""
    from linkedin.models import WatchedSource, Campaign
    campaign = Campaign.objects.create(name="TestCampaign", campaign_objective="test")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="test-company",
        display_name="Test Company",
    )
    # Field should exist and default to None
    assert source.last_successful_poll_at is None


@pytest.mark.django_db
def test_last_successful_poll_at_can_be_set():
    """last_successful_poll_at can be set to a datetime."""
    from linkedin.models import WatchedSource, Campaign
    campaign = Campaign.objects.create(name="TestCampaign2", campaign_objective="test")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="test-company-2",
        display_name="Test Company 2",
    )
    now = timezone.now()
    source.last_successful_poll_at = now
    source.save(update_fields=["last_successful_poll_at"])
    source.refresh_from_db()
    assert source.last_successful_poll_at is not None