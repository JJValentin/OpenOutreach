"""
Tests for account health helper and signal recompute.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.utils import timezone as dj_timezone

from crm.models import Deal
from linkedin.models import Campaign, Signal, Task, WatchedSource
from linkedin.tasks.recompute_signal_scores import handle_recompute_signal_scores
from linkedin.signals.polling import is_account_health_ok


class TestAccountHealthHelper:
    def test_is_account_health_ok_returns_true_when_no_linkedin_profile(self, db):
        """When no LinkedInProfile attached, returns True (allow polling)."""
        from linkedin.models import Campaign, WatchedSource
        campaign = Campaign.objects.create(name="Test Campaign")
        source = WatchedSource.objects.create(
            campaign=campaign,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="test",
            display_name="Test",
        )
        result = is_account_health_ok(source)
        assert result is True

    def test_is_account_health_ok_returns_true_when_no_budget_exhaustion(self, db):
        """When budget is NOT exhausted (no actions taken), returns True."""
        from linkedin.models import Campaign, WatchedSource, LinkedInProfile
        from django.contrib.auth.models import User

        user = User.objects.create(username="testuser2")
        campaign = Campaign.objects.create(name="Test Campaign2")
        campaign.users.add(user)
        LinkedInProfile.objects.create(
            user=user,
            linkedin_username="test",
            linkedin_password="test",
            connect_daily_limit=20,
        )
        source = WatchedSource.objects.create(
            campaign=campaign,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="test",
            display_name="Test",
        )
        result = is_account_health_ok(source)
        assert result is True


class TestRecomputeSignalScores:
    def test_recompute_adds_score_from_fresh_signal(self, db):
        """Recompute calculates composite score from recent signals."""
        from tests.factories import LeadFactory, DealFactory, WatchedSourceFactory, SignalFactory, Signal as SignalModel

        campaign = Campaign.objects.create(name="Recompute Test Campaign")
        lead = LeadFactory(urn="urn:li:person:rescore", public_identifier="rescore")
        deal = DealFactory(lead=lead, campaign=campaign, signal_sourced=True, composite_signal_score=0)
        ws = WatchedSourceFactory(campaign=campaign, kind=WatchedSource.Kind.OWN_PROFILE, display_name="Test")
        # Create a fresh signal (created_at = now)
        sig = SignalFactory(
            campaign=campaign,
            watched_source=ws,
            profile_urn="urn:li:person:rescore",
            post_urn="urn:li:post:fresh",
            engagement_type=SignalModel.EngagementType.COMMENT,
            score=100,
        )
        # Manually set created_at to be recent (within 30 days)
        Signal.objects.filter(id=sig.id).update(created_at=datetime.now(timezone.utc))

        task = Task.objects.create(
            task_type=Task.TaskType.RECOMPUTE_SIGNAL_SCORES,
            status=Task.Status.PENDING,
            scheduled_at=dj_timezone.now(),
            payload={},
        )

        class _FakeSession:
            def __init__(self, camp):
                self.campaign = camp
                self.campaigns = [camp]

        handle_recompute_signal_scores(task, _FakeSession(campaign), qualifiers={})
        deal.refresh_from_db()
        # COMMENT on own_post: base=100, recency=1.0 (fresh), frequency bonus=0 (1 event)
        # total = 100 * 1.0 + 0 = 100
        assert deal.composite_signal_score == 100

    def test_recompute_idempotent(self, db):
        """Running recompute twice produces same result."""
        from tests.factories import LeadFactory, DealFactory, WatchedSourceFactory, SignalFactory, Signal as SignalModel

        campaign = Campaign.objects.create(name="Idempotent Test Campaign B")
        lead = LeadFactory(urn="urn:li:person:idemp2", public_identifier="idemp2")
        deal = DealFactory(lead=lead, campaign=campaign, signal_sourced=True, composite_signal_score=0)
        ws = WatchedSourceFactory(campaign=campaign, kind=WatchedSource.Kind.OWN_PROFILE, display_name="Test B")
        sig = SignalFactory(
            campaign=campaign,
            watched_source=ws,
            profile_urn="urn:li:person:idemp2",
            post_urn="urn:li:post:idemp2",
            engagement_type=SignalModel.EngagementType.REACTION,
            score=85,
        )
        Signal.objects.filter(id=sig.id).update(created_at=datetime.now(timezone.utc))

        task = Task.objects.create(
            task_type=Task.TaskType.RECOMPUTE_SIGNAL_SCORES,
            status=Task.Status.PENDING,
            scheduled_at=dj_timezone.now(),
            payload={},
        )

        class _FakeSession:
            def __init__(self, camp):
                self.campaign = camp
                self.campaigns = [camp]

        sess = _FakeSession(campaign)
        handle_recompute_signal_scores(task, sess, qualifiers={})
        deal.refresh_from_db()
        score_after_first = deal.composite_signal_score
        assert score_after_first == 85, f"First call expected 85, got {score_after_first}"

        handle_recompute_signal_scores(task, sess, qualifiers={})
        deal.refresh_from_db()
        score_after_second = deal.composite_signal_score
        assert score_after_second == score_after_first, f"Second call {score_after_second} != first {score_after_first}"
