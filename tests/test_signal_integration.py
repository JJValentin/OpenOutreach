"""End-to-end integration test: poll -> signal -> inject -> ranked Deal -> rendered prompt.

Mocked — NO live Voyager/LinkedIn calls, NO LLM API calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import numpy as np

from datetime import datetime, timezone, timedelta

from linkedin.models import Campaign, Signal, WatchedSource
from linkedin.signals.polling import poll_watched_source
from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles
from linkedin.agents._signal_context import build_signal_context
from linkedin.ml.qualifier import BayesianQualifier, _rank_by_score
from linkedin.conf import SIGNAL_PRIORITY_THRESHOLD, SIGNAL_MAX_ACQUISITION_BONUS

from crm.models import Deal, Lead

from tests.factories import CampaignFactory, WatchedSourceFactory, SignalFactory


# ---------------------------------------------------------------------------
# Mock data builders
# ---------------------------------------------------------------------------

def make_mock_post(urn, excerpt="Test post", published_at=None, idx=1):
    if published_at is None:
        published_at = datetime.now(timezone.utc) - timedelta(hours=idx)
    return {
        "post_urn": urn,
        "post_excerpt": excerpt,
        "published_at": published_at.isoformat(),
        "author_urn": f"urn:li:fs_member:{idx}",
        "author_public_id": f"author{idx}",
    }


def make_mock_reactors(urns_and_types):
    return [
        {"profile_urn": urn, "public_identifier": f"user{i}", "reaction_type": rtype}
        for (i, (urn, rtype)) in enumerate(urns_and_types, start=1)
    ]


def make_mock_comments(profile_urns_and_texts):
    return [
        {"profile_urn": urn, "public_identifier": f"commenter{i}", "comment_text": text}
        for (i, (urn, text)) in enumerate(profile_urns_and_texts, start=1)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def campaign(db):
    return CampaignFactory()


@pytest.fixture
def watched_source(db, campaign):
    return WatchedSourceFactory(
        campaign=campaign,
        kind=WatchedSource.Kind.OWN_PROFILE,
        identifier="testuser",
        display_name="Test User",
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestPollSignalInjectPipeline:
    """Full pipeline: poll -> signal -> inject -> deal -> prompt context."""

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_poll_creates_above_threshold_signal(
        self, watched_source, campaign, fake_session
    ):
        """Step 1 + 2: Poll creates a Signal with score >= threshold."""
        post = make_mock_post("urn:li:fs_post:123", excerpt="Exciting news!", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:999", "LIKE")])
        comments = make_mock_comments([("urn:li:fs_member:888", "Great post!")])

        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments_fn, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments_fn.return_value = comments
            mock_reposts.return_value = []

            result = poll_watched_source(watched_source, fake_session)

        assert result.posts_seen == 1
        assert result.engagers_seen == 2
        assert result.signals_created == 2  # one reaction + one comment

        # At least one signal must have score >= threshold
        signal = Signal.objects.filter(
            campaign=campaign, profile_urn="urn:li:fs_member:999"
        ).first()
        assert signal is not None
        assert signal.score >= SIGNAL_PRIORITY_THRESHOLD

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_inject_creates_lead_and_deal(
        self, watched_source, campaign, fake_session
    ):
        """Step 3 + 4: inject_signal_profiles creates Lead + Deal with metadata."""
        post = make_mock_post("urn:li:fs_post:INJ001", excerpt="Big announcement!", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:JANE001", "LIKE")])

        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []

            poll_watched_source(watched_source, fake_session)

        # Run the injection handler
        handle_inject_signal_profiles(campaign)

        # Lead + Deal must exist
        lead = Lead.objects.filter(urn="urn:li:fs_member:JANE001").first()
        assert lead is not None, "Lead should be created"
        assert lead.disqualified is False

        deal = Deal.objects.filter(lead=lead, campaign=campaign).first()
        assert deal is not None, "Deal should be created"
        assert deal.signal_sourced is True, "Deal must be marked signal-sourced"
        assert deal.composite_signal_score > 0, "Composite score must be > 0"
        assert deal.signal_metadata != {}, "Signal metadata must be populated"

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_build_signal_context_renders_warm_block(
        self, watched_source, campaign, fake_session
    ):
        """Step 5: build_signal_context returns a non-empty warm-signal block."""
        post = make_mock_post("urn:li:fs_post:CTX001", excerpt="Launch day news!", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:CTXUSER", "LIKE")])

        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []

            poll_watched_source(watched_source, fake_session)
            handle_inject_signal_profiles(campaign)

        lead = Lead.objects.filter(urn="urn:li:fs_member:CTXUSER").first()
        deal = Deal.objects.get(lead=lead, campaign=campaign)

        context = build_signal_context(deal)
        assert context is not None, "Signal context must not be None"
        assert "Test User" in context, "Display name must appear in context"
        assert "Launch day news!" in context, "Post excerpt must appear in context"


class TestSignalDealRanksFirst:
    """Step 6: signal Deal ranks above zero-signal Deal."""

    def _make_trained_qualifier(self, seed=42, campaign=None):
        """Build a BayesianQualifier with enough data to produce real scores."""
        qualifier = BayesianQualifier(seed=seed, campaign=campaign)
        rng = np.random.RandomState(seed)
        for _ in range(5):
            qualifier.update(rng.randn(384).astype(np.float32) + 1.0, 1)
            qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
        return qualifier

    def test_signal_deal_ranks_first_over_zero_signal_deal(self, db, fake_session):
        """A Deal with composite_signal_score ranks above an identical Deal with zero signal."""
        from linkedin.models import Campaign
        from tests.factories import LeadFactory, DealFactory

        campaign = Campaign.objects.create(name="ranking-test-campaign")

        lead_signal = LeadFactory(public_identifier="signal_lead")
        lead_cold = LeadFactory(public_identifier="cold_lead")

        # Identical embeddings so raw scores are equal; signal bonus differentiates
        zero_emb = np.zeros(384, dtype=np.float32).tobytes()
        lead_signal.embedding = zero_emb
        lead_signal.save()
        lead_cold.embedding = zero_emb
        lead_cold.save()

        deal_signal = DealFactory(lead=lead_signal, campaign=campaign)
        deal_signal.composite_signal_score = 150  # high signal
        deal_signal.save()

        deal_cold = DealFactory(lead=lead_cold, campaign=campaign)
        deal_cold.composite_signal_score = 0
        deal_cold.save()

        qualifier = self._make_trained_qualifier(campaign=campaign)
        profiles = [
            {"lead_id": lead_signal.pk, "public_identifier": "signal_lead"},
            {"lead_id": lead_cold.pk, "public_identifier": "cold_lead"},
        ]

        result = qualifier.rank_profiles(profiles, fake_session)

        # Signal Deal must rank first despite identical embeddings
        assert result[0]["lead_id"] == lead_signal.pk
        assert result[1]["lead_id"] == lead_cold.pk

    def test_no_duplicate_deals_after_re_run(self, db, fake_session):
        """Re-running inject is idempotent — no duplicate Deals."""
        from linkedin.models import Campaign
        from tests.factories import LeadFactory, DealFactory

        campaign = Campaign.objects.create(name="idempotent-test")

        lead = LeadFactory(urn="urn:li:person:alice", public_identifier="alice")
        deal = DealFactory(lead=lead, campaign=campaign)

        signal = SignalFactory(
            campaign=campaign,
            watched_source=WatchedSourceFactory(campaign=campaign),
            profile_urn=lead.urn,
            score=75,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:idempotent",
        )

        handle_inject_signal_profiles(campaign)
        first_count = Deal.objects.count()

        handle_inject_signal_profiles(campaign)
        second_count = Deal.objects.count()

        assert first_count == second_count, "No new deals on re-run"
