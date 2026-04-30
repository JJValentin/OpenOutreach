"""Tests for inject_signal_profiles task handler and enqueuer."""
from unittest.mock import patch

import pytest
from django.utils import timezone

from linkedin.models import Campaign, Signal, Task
from crm.models import Deal, Lead


@pytest.fixture
def campaign(db):
    from tests.factories import CampaignFactory
    return CampaignFactory()


@pytest.fixture
def watched_source(db, campaign):
    from tests.factories import WatchedSourceFactory, Signal
    return WatchedSourceFactory(
        campaign=campaign,
        kind=Signal.Kind.OWN_POST_ENGAGEMENT,
        display_name="Test Co",
    )


@pytest.fixture
def high_score_signal(db, campaign, watched_source):
    """Signal with score above threshold (threshold=50 by default)."""
    from tests.factories import SignalFactory, Signal as SignalModel
    return SignalFactory(
        campaign=campaign,
        watched_source=watched_source,
        profile_urn="urn:li:person:alice",
        score=75,
        engagement_type=SignalModel.EngagementType.COMMENT,
        post_urn="urn:li:post:1",
    )


@pytest.fixture
def low_score_signal(db, campaign, watched_source):
    """Signal with score below threshold."""
    from tests.factories import SignalFactory, Signal as SignalModel
    return SignalFactory(
        campaign=campaign,
        watched_source=watched_source,
        profile_urn="urn:li:person:bob",
        score=10,
        engagement_type=SignalModel.EngagementType.REACTION,
        post_urn="urn:li:post:2",
    )


class TestHandleInjectSignalProfiles:
    """Test handle_inject_signal_profiles."""

    def test_creates_lead_and_deal_for_new_profile(
        self, db, campaign, high_score_signal
    ):
        """New profile + signal at score >= threshold creates Lead + Deal."""
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        deal_count_before = Deal.objects.count()
        lead_count_before = Lead.objects.count()

        handle_inject_signal_profiles(campaign)

        lead = Lead.objects.filter(urn="urn:li:person:alice").first()
        assert lead is not None, "Lead should be created for new profile"
        assert lead.disqualified is False

        deal = Deal.objects.filter(lead=lead, campaign=campaign).first()
        assert deal is not None, "Deal should be created"
        assert deal.signal_sourced is True
        assert deal.composite_signal_score > 0
        assert deal.signal_metadata != {}

    def test_existing_deal_gets_metadata_merged(
        self, db, campaign, watched_source
    ):
        """Existing Deal gets metadata merged; existing state preserved."""
        from tests.factories import LeadFactory, DealFactory, SignalFactory, Signal as SignalModel
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        lead = LeadFactory(urn="urn:li:person:alice", public_identifier="alice")
        deal = DealFactory(lead=lead, campaign=campaign, state="PENDING")
        original_state = deal.state

        SignalFactory(
            campaign=campaign,
            watched_source=watched_source,
            profile_urn="urn:li:person:alice",
            score=75,
            engagement_type=SignalModel.EngagementType.COMMENT,
            post_urn="urn:li:post:1",
        )

        handle_inject_signal_profiles(campaign)

        deal.refresh_from_db()
        assert deal.signal_sourced is True
        assert deal.composite_signal_score > 0
        assert deal.signal_metadata != {}
        assert deal.state == original_state

    def test_disqualified_lead_is_skipped(
        self, db, campaign, watched_source
    ):
        """Disqualified Lead is skipped."""
        from tests.factories import LeadFactory, SignalFactory, Signal as SignalModel
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        lead = LeadFactory(
            urn="urn:li:person:alice",
            public_identifier="alice",
            disqualified=True,
        )
        SignalFactory(
            campaign=campaign,
            watched_source=watched_source,
            profile_urn="urn:li:person:alice",
            score=75,
            engagement_type=SignalModel.EngagementType.COMMENT,
            post_urn="urn:li:post:1",
        )

        handle_inject_signal_profiles(campaign)

        assert not Deal.objects.filter(lead=lead, campaign=campaign).exists()

    def test_score_below_threshold_is_skipped(self, db, campaign, low_score_signal):
        """Score below threshold is skipped."""
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        handle_inject_signal_profiles(campaign)

        assert not Lead.objects.filter(urn="urn:li:person:bob").exists()

    def test_idempotent_rerun_no_duplicate_deals(
        self, db, campaign, high_score_signal
    ):
        """Re-running is idempotent: no duplicate Deal/Lead."""
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        handle_inject_signal_profiles(campaign)
        first_deal_count = Deal.objects.count()

        handle_inject_signal_profiles(campaign)
        second_deal_count = Deal.objects.count()

        assert first_deal_count == second_deal_count, "No new deals on re-run"

        lead = Lead.objects.get(urn="urn:li:person:alice")
        assert Deal.objects.filter(lead=lead, campaign=campaign).count() == 1

    def test_no_duplicate_outreach_tasks_created(
        self, db, campaign, high_score_signal
    ):
        """handle_inject_signal_profiles does NOT create outreach tasks."""
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        handle_inject_signal_profiles(campaign)

        tasks_created = Task.objects.filter(
            task_type__in=[
                Task.TaskType.CONNECT,
                Task.TaskType.FOLLOW_UP,
                Task.TaskType.CHECK_PENDING,
            ],
            status=Task.Status.PENDING,
        )
        assert tasks_created.count() == 0, "inject_signal_profiles should not create outreach tasks"


class TestEnqueueInjectSignalProfiles:
    """Test enqueue_inject_signal_profiles scheduler function."""

    def test_enqueue_creates_pending_task(self, db, campaign):
        """enqueue_inject_signal_profiles creates a PENDING task."""
        from linkedin.tasks.inject_signal_profiles import enqueue_inject_signal_profiles

        enqueue_inject_signal_profiles(campaign.id)

        new_tasks = Task.objects.filter(
            task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
            status=Task.Status.PENDING,
            payload__campaign_id=campaign.id,
        )
        assert new_tasks.count() == 1, "Should create INJECT_SIGNAL_PROFILES PENDING task"

    def test_enqueue_is_deduplicated(self, db, campaign):
        """Multiple enqueue calls produce only one PENDING task."""
        from linkedin.tasks.inject_signal_profiles import enqueue_inject_signal_profiles

        enqueue_inject_signal_profiles(campaign.id)
        enqueue_inject_signal_profiles(campaign.id)

        tasks = Task.objects.filter(
            task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
            status=Task.Status.PENDING,
        )
        assert tasks.count() == 1, "Should be deduplicated to one PENDING task"

    def test_handler_runs_without_error(self, db, campaign, high_score_signal):
        """Handler completes without error on valid input."""
        from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles

        Task.objects.create(
            task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
            status=Task.Status.PENDING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": campaign.id},
        )

        handle_inject_signal_profiles(campaign)

        assert Task.objects.filter(
            task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
            status=Task.Status.COMPLETED,
        ).exists()