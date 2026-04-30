"""Tests for signal scoring functions."""
from datetime import datetime, timedelta, timezone
import pytest
from linkedin.signals.scoring import (
    compute_base_score,
    compute_recency_multiplier,
    compute_frequency_bonus,
    cap_composite_score,
    compute_composite_score,
)


class TestComputeBaseScore:
    """Test base score computation per engagement type and source kind."""

    def test_own_post_comment(self):
        assert compute_base_score("comment", "own_post") == 100

    def test_own_post_reaction(self):
        assert compute_base_score("reaction", "own_post") == 85

    def test_own_post_repost(self):
        assert compute_base_score("repost", "own_post") == 70

    def test_competitor_comment(self):
        assert compute_base_score("comment", "competitor") == 75

    def test_competitor_reaction(self):
        assert compute_base_score("reaction", "competitor") == 55

    def test_competitor_repost(self):
        assert compute_base_score("repost", "competitor") == 70

    def test_influencer_reaction(self):
        assert compute_base_score("reaction", "influencer") == 55

    def test_influencer_comment(self):
        assert compute_base_score("comment", "influencer") == 70

    def test_influencer_repost(self):
        assert compute_base_score("repost", "influencer") == 70


class TestComputeRecencyMultiplier:
    """Test recency multiplier tiers."""

    def test_within_3_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=2, hours=23)
        assert compute_recency_multiplier(event_time, now=now) == 1.0

    def test_exactly_3_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=3)
        assert compute_recency_multiplier(event_time, now=now) == 1.0

    def test_within_10_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=5)
        assert compute_recency_multiplier(event_time, now=now) == 0.75

    def test_exactly_10_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=10)
        assert compute_recency_multiplier(event_time, now=now) == 0.75

    def test_within_25_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=15)
        assert compute_recency_multiplier(event_time, now=now) == 0.5

    def test_exactly_25_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=25)
        assert compute_recency_multiplier(event_time, now=now) == 0.5

    def test_beyond_25_days(self):
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        event_time = now - timedelta(days=45)
        assert compute_recency_multiplier(event_time, now=now) == 0.2


class TestComputeFrequencyBonus:
    """Test frequency bonus within 30 days window."""

    def test_single_event(self):
        assert compute_frequency_bonus(1) == 0

    def test_two_events(self):
        assert compute_frequency_bonus(2) == 10

    def test_three_events(self):
        assert compute_frequency_bonus(3) == 15

    def test_ten_events(self):
        assert compute_frequency_bonus(10) == 20

    def test_many_events_capped(self):
        assert compute_frequency_bonus(50) == 20


class TestCapCompositeScore:
    """Test composite score capping."""

    def test_under_cap(self):
        assert cap_composite_score(100) == 100

    def test_at_cap(self):
        assert cap_composite_score(150) == 150

    def test_over_cap(self):
        assert cap_composite_score(200) == 150

    def test_negative_clamped(self):
        # Negative scores shouldn't occur but verify behavior
        assert cap_composite_score(-10) == 0


class TestComputeCompositeScore:
    """Test full composite score computation."""

    def test_single_recent_own_post_comment(self):
        """Single comment on own post within 3 days: 100 * 1.0 + 0 = 100"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = [("comment", "own_post", now - timedelta(days=1))]
        assert compute_composite_score(events, now=now) == 100

    def test_competitor_reaction_with_recency(self):
        """Competitor reaction at 5 days: 55 * 0.75 + 0 = 41.25"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = [("reaction", "competitor", now - timedelta(days=5))]
        result = compute_composite_score(events, now=now)
        assert result == 41  # Integer truncated

    def test_multiple_events_frequency_bonus(self):
        """2 events with frequency bonus: (100*1.0 + 85*1.0) + 10 = 195 -> capped to 150"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = [
            ("comment", "own_post", now - timedelta(days=1)),
            ("reaction", "own_post", now - timedelta(days=2)),
        ]
        result = compute_composite_score(events, now=now)
        assert result == 150  # Capped

    def test_high_recency_bonus(self):
        """3 events at max recency: 3*(100*1.0) + 15 = 315 -> capped to 150"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = [
            ("comment", "own_post", now - timedelta(days=1)),
            ("comment", "own_post", now - timedelta(days=2)),
            ("comment", "own_post", now - timedelta(days=3)),
        ]
        result = compute_composite_score(events, now=now)
        assert result == 150  # Capped

    def test_empty_events(self):
        """No events yields zero score"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = []
        assert compute_composite_score(events, now=now) == 0

    def test_old_events_low_multiplier(self):
        """Old events get 0.2 multiplier"""
        now = datetime(2026, 4, 29, tzinfo=timezone.utc)
        events = [("comment", "competitor", now - timedelta(days=30))]
        result = compute_composite_score(events, now=now)
        # 75 * 0.2 = 15
        assert result == 15
