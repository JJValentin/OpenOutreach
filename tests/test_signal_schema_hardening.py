import pytest
from django.db import IntegrityError

from linkedin.models import Campaign, WatchedSource, Signal
from linkedin.admin import WatchedSourceAdmin, WatchedSourceForm


# 1. Signal retains row when watched_source is deleted (SET_NULL)

@pytest.mark.django_db
def test_signal_retains_row_when_watched_source_deleted(db):
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    source = WatchedSource.objects.create(
        campaign=campaign,
        kind=WatchedSource.Kind.OWN_PROFILE,
        identifier="me",
    )
    signal = Signal.objects.create(
        campaign=campaign,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )
    signal_pk = signal.pk
    source.delete()
    signal.refresh_from_db()
    assert signal.pk == signal_pk
    assert signal.watched_source is None


# 2. Signal allows same (profile_urn, post_urn, engagement_type) across campaigns

@pytest.mark.django_db
def test_signal_allows_same_profile_post_type_different_campaigns(db):
    campaign_a = Campaign.objects.create(name="Radar A", campaign_objective="Find warm leads")
    campaign_b = Campaign.objects.create(name="Radar B", campaign_objective="Find warm leads")
    source_a = WatchedSource.objects.create(
        campaign=campaign_a, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me",
    )
    source_b = WatchedSource.objects.create(
        campaign=campaign_b, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me",
    )
    Signal.objects.create(
        campaign=campaign_a,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source_a,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )
    Signal.objects.create(
        campaign=campaign_b,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source_b,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )


# 3. Signal rejects duplicate (campaign, profile_urn, post_urn, engagement_type)

@pytest.mark.django_db
def test_signal_rejects_duplicate_campaign_profile_post_engagement(db):
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    source = WatchedSource.objects.create(
        campaign=campaign, kind=WatchedSource.Kind.OWN_PROFILE, identifier="me",
    )
    Signal.objects.create(
        campaign=campaign,
        profile_urn="urn:li:fsd_profile:1",
        watched_source=source,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        engagement_type=Signal.EngagementType.REACTION,
        post_urn="urn:li:activity:1",
    )
    with pytest.raises(IntegrityError):
        Signal.objects.create(
            campaign=campaign,
            profile_urn="urn:li:fsd_profile:1",
            watched_source=source,
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:activity:1",
        )


# 4. Deal defaults: signal_metadata == {}, composite_signal_score == 0, signal_sourced == False

@pytest.mark.django_db
def test_deal_signal_metadata_default_empty_dict(db):
    from crm.models import Deal, Lead
    lead = Lead.objects.create(public_identifier="alice", linkedin_url="https://www.linkedin.com/in/alice/")
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    deal = Deal.objects.create(lead=lead, campaign=campaign)
    assert deal.signal_metadata == {}

@pytest.mark.django_db
def test_deal_composite_signal_score_default_zero(db):
    from crm.models import Deal, Lead
    lead = Lead.objects.create(public_identifier="bob", linkedin_url="https://www.linkedin.com/in/bob/")
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    deal = Deal.objects.create(lead=lead, campaign=campaign)
    assert deal.composite_signal_score == 0

@pytest.mark.django_db
def test_deal_signal_sourced_default_false(db):
    from crm.models import Deal, Lead
    lead = Lead.objects.create(public_identifier="carol", linkedin_url="https://www.linkedin.com/in/carol/")
    campaign = Campaign.objects.create(name="Radar", campaign_objective="Find warm leads")
    deal = Deal.objects.create(lead=lead, campaign=campaign)
    assert deal.signal_sourced is False


# 5. Admin form rejects non-LinkedIn URL via clean_identifier

@pytest.mark.django_db
def test_admin_form_clean_identifier_rejects_non_linkedin(db):
    """WatchedSourceForm.clean_identifier() raises ValueError for non-LinkedIn URLs."""
    # Test clean_identifier directly with an invalid URL
    form_data = {
        'campaign': 1,
        'kind': 'competitor_company',
        'identifier': 'https://twitter.com/acme',
        'display_name': '',
        'cadence_minutes': 120,
        'is_active': True,
    }
    form = WatchedSourceForm(data=form_data)
    # Simulate that the form has cleaned_data by pre-populating it
    form.cleaned_data = form_data.copy()
    # clean_identifier should raise ValueError for non-LinkedIn URL
    with pytest.raises(ValueError, match="LinkedIn"):
        form.clean_identifier()