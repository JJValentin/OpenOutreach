import pytest
from django.db import IntegrityError

from linkedin.models import Campaign, WatchedSource, Signal


@pytest.mark.django_db
def test_watched_source_defaults(db):
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="acme-inc",
        display_name="Acme",
    )
    assert source.is_active is True
    assert source.consecutive_failures == 0
    assert source.cadence_minutes == 120


@pytest.mark.django_db
def test_signal_campaign_scoped_unique(db):
    """Signal unique constraint is scoped per campaign, not global."""
    campaign_a = Campaign.objects.create(name="Radar A", campaign_objective="Find warm leads")
    campaign_b = Campaign.objects.create(name="Radar B", campaign_objective="Find warm leads")
    source_a = WatchedSource.objects.create(
        campaign=campaign_a, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me",
    )
    source_b = WatchedSource.objects.create(
        campaign=campaign_b, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me",
    )
    # First signal in campaign_a -- should succeed
    Signal.objects.create(
        campaign=campaign_a,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source_a,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )
    # Same tuple in campaign_b -- should succeed (campaign-scoped)
    Signal.objects.create(
        campaign=campaign_b,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source_b,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )
    # Duplicate in same campaign_a -- should raise IntegrityError
    with pytest.raises(IntegrityError):
        Signal.objects.create(
            campaign=campaign_a,
            profile_urn="urn:li:fsd_profile:1",
            watched_source=source_a,
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:activity:1",
        )