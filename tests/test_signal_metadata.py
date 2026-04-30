"""Tests for signal metadata builder."""
from datetime import datetime, timedelta, timezone

import pytest

from linkedin.models import Signal, WatchedSource
from linkedin.signals.metadata import build_signal_metadata, update_deal_signal_metadata
from tests.factories import CampaignFactory, DealFactory, LeadFactory, UserFactory, WatchedSourceFactory, SignalFactory


@pytest.fixture
def campaign(db):
    return CampaignFactory()


@pytest.fixture
def watched_sources(db, campaign):
    own = WatchedSourceFactory(campaign=campaign, kind=WatchedSource.Kind.OWN_PROFILE, display_name="My Co")
    competitor = WatchedSourceFactory(campaign=campaign, kind=WatchedSource.Kind.COMPETITOR_COMPANY, display_name="Acme Corp")
    influencer = WatchedSourceFactory(campaign=campaign, kind=WatchedSource.Kind.INFLUENCER_PROFILE, identifier="urn:li:person:abc")
    return {"own": own, "competitor": competitor, "influencer": influencer}


class TestBuildSignalMetadata:
    """Test build_signal_metadata function."""

    def test_returns_at_most_max_signals(self, db, campaign, watched_sources):
        """Returns at most max_signals entries."""
        # Create 10 signals
        now = datetime.now(timezone.utc)
        signals = []
        for i in range(10):
            sig = SignalFactory(
                campaign=campaign,
                watched_source=watched_sources["own"],
                kind=Signal.Kind.OWN_POST_ENGAGEMENT,
                engagement_type=Signal.EngagementType.COMMENT,
                post_urn=f"urn:li:post:{i}",
            )
            # Manually set created_at to ensure distinct timestamps (avoids auto_now_add override)
            sig.created_at = now - timedelta(days=i)
            sig.save(update_fields=["created_at"])
            signals.append(sig)
        qs = Signal.objects.all()
        result = build_signal_metadata(qs, max_signals=5)
        assert len(result["signals"]) == 5

    def test_sort_order_most_recent_first_ties_by_score(self, db, campaign, watched_sources):
        """Most recent first; ties broken by score descending."""
        now = datetime.now(timezone.utc)
        # Signal A: created today, score 10
        s1 = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:s1",
            score=10,
        )
        s1.created_at = now
        s1.save(update_fields=["created_at"])

        # Signal B: created 1 day ago, score 100
        s2 = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:s2",
            score=100,
        )
        s2.created_at = now - timedelta(days=1)
        s2.save(update_fields=["created_at"])

        # Signal C: created 2 days ago, score 50
        s3 = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:s3",
            score=50,
        )
        s3.created_at = now - timedelta(days=2)
        s3.save(update_fields=["created_at"])

        result = build_signal_metadata([s1, s2, s3], max_signals=3)
        post_urns = [sig["post_urn"] for sig in result["signals"]]
        # Most recent (s1) should be first, then s2 (higher score than s3), then s3
        assert post_urns == ["urn:li:post:s1", "urn:li:post:s2", "urn:li:post:s3"]

    def test_comment_text_truncated_to_2000(self, db, campaign, watched_sources):
        """comment_text longer than 2000 chars is truncated."""
        long_comment = "x" * 3000
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.COMMENT,
            post_urn="urn:li:post:comment_test",
            payload_json={"comment_text": long_comment},
        )
        result = build_signal_metadata([sig])
        assert len(result["signals"][0]["comment_text"]) == 2000

    def test_post_excerpt_truncated_to_240(self, db, campaign, watched_sources):
        """post_excerpt longer than 240 chars is truncated."""
        long_excerpt = "y" * 300
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:excerpt_test",
            post_excerpt=long_excerpt,
        )
        result = build_signal_metadata([sig])
        assert len(result["signals"][0]["post_excerpt"]) == 240

    def test_empty_input_returns_zero_score_empty_list(self, db):
        """Empty input returns {composite_score: 0, signals: []}."""
        result = build_signal_metadata([])
        assert result == {"composite_score": 0, "signals": []}

    def test_no_watched_source_empty_source_name(self, db, campaign):
        """Signal without watched_source has empty source_name."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=None,
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:no_source",
        )
        result = build_signal_metadata([sig])
        assert result["signals"][0]["source_name"] == ""
        assert result["signals"][0]["source_kind"] == "own_post"

    def test_source_kind_from_watched_source(self, db, campaign, watched_sources):
        """source_kind is derived from watched_source.kind."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["competitor"],
            kind=Signal.Kind.COMPETITOR_ENGAGEMENT,
            engagement_type=Signal.EngagementType.COMMENT,
            post_urn="urn:li:post:ws_kind",
        )
        result = build_signal_metadata([sig])
        assert result["signals"][0]["source_kind"] == "competitor"

    def test_timestamp_iso_8601_utc(self, db, campaign, watched_sources):
        """timestamp is ISO 8601 UTC string."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:ts_test",
        )
        result = build_signal_metadata([sig])
        ts = result["signals"][0]["timestamp"]
        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


class TestUpdateDealSignalMetadata:
    """Test update_deal_signal_metadata function."""

    @pytest.fixture
    def deal(self, db):
        user = UserFactory()
        campaign = CampaignFactory()
        lead = LeadFactory()
        return DealFactory(lead=lead, campaign=campaign)

    def test_writes_signal_metadata(self, db, campaign, watched_sources, deal):
        """Writes signal_metadata JSON to deal."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.COMMENT,
            post_urn="urn:li:post:deal_test",
            post_excerpt="Test excerpt",
            payload_json={"comment_text": "Great post!"},
        )
        update_deal_signal_metadata(deal, [sig])
        deal.refresh_from_db()
        assert deal.signal_metadata["composite_score"] >= 0
        assert len(deal.signal_metadata["signals"]) == 1
        assert deal.signal_metadata["signals"][0]["post_urn"] == "urn:li:post:deal_test"

    def test_sets_composite_signal_score(self, db, campaign, watched_sources, deal):
        """Sets composite_signal_score on deal."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.COMMENT,
            post_urn="urn:li:post:score_test",
        )
        update_deal_signal_metadata(deal, [sig])
        deal.refresh_from_db()
        assert deal.composite_signal_score >= 0

    def test_sets_signal_sourced_flag(self, db, campaign, watched_sources, deal):
        """Sets signal_sourced = True on deal."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:flag_test",
        )
        update_deal_signal_metadata(deal, [sig])
        deal.refresh_from_db()
        assert deal.signal_sourced is True

    def test_idempotent_on_repeat_calls(self, db, campaign, watched_sources, deal):
        """Safe to call repeatedly with same signal set."""
        sig = SignalFactory(
            campaign=campaign,
            watched_source=watched_sources["own"],
            kind=Signal.Kind.OWN_POST_ENGAGEMENT,
            engagement_type=Signal.EngagementType.REACTION,
            post_urn="urn:li:post:idempotent_test",
            score=10,
        )
        update_deal_signal_metadata(deal, [sig])
        update_deal_signal_metadata(deal, [sig])
        deal.refresh_from_db()
        first_call = dict(deal.signal_metadata)
        update_deal_signal_metadata(deal, [sig])
        deal.refresh_from_db()
        assert deal.signal_metadata == first_call
        assert deal.signal_sourced is True
