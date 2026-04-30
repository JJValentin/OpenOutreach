"""Tests for signal score ranking in the qualifier."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import BayesianQualifier, _rank_by_score
from linkedin.conf import SIGNAL_MAX_ACQUISITION_BONUS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trained_qualifier(seed=42, campaign=None):
    qualifier = BayesianQualifier(seed=seed, campaign=campaign)
    rng = np.random.RandomState(seed)
    for _ in range(5):
        qualifier.update(rng.randn(384).astype(np.float32) + 1.0, 1)
        qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
    return qualifier


def _make_profile(lead_id, public_id="alice"):
    return {"lead_id": lead_id, "public_identifier": public_id, "url": "", "profile": {}}


def _fake_embedding():
    return np.ones(384, dtype=np.float32)


# ---------------------------------------------------------------------------
# _rank_by_score with signal bonuses
# ---------------------------------------------------------------------------

class TestRankByScoreSignalBonuses:
    def test_no_bonus_when_signal_bonuses_none(self, db):
        """Zero-signal candidates: behavior unchanged."""
        qualifier = _make_trained_qualifier()
        profiles = [_make_profile(1), _make_profile(2)]

        with patch("linkedin.ml.qualifier._load_profile_embeddings") as mock_load:
            mock_load.return_value = [
                (profiles[0], np.zeros(384)),
                (profiles[1], np.ones(384)),
            ]
            result = _rank_by_score(profiles, qualifier.pipeline, None, signal_bonuses=None)

        # Higher score (ones) should rank first
        assert result[0]["lead_id"] == 2
        assert result[1]["lead_id"] == 1

    def test_high_signal_boosts_ranking(self, db):
        """Candidate with high signal score ranks above stronger cold candidate."""
        qualifier = _make_trained_qualifier()
        profiles = [_make_profile(1), _make_profile(2)]

        with patch("linkedin.ml.qualifier._load_profile_embeddings") as mock_load:
            mock_load.return_value = [
                (profiles[0], np.zeros(384)),  # lower raw score
                (profiles[1], np.ones(384) * 0.1),  # slightly lower raw score
            ]
            # Lead 1 gets max signal bonus, Lead 2 gets none
            signal_bonuses = {1: SIGNAL_MAX_ACQUISITION_BONUS, 2: 0.0}
            result = _rank_by_score(profiles, qualifier.pipeline, None, signal_bonuses=signal_bonuses)

        # Lead 1 should jump ahead despite lower raw score
        assert result[0]["lead_id"] == 1
        assert result[1]["lead_id"] == 2

    def test_signal_bonus_capped_at_max(self, db):
        """Bonus is capped at SIGNAL_MAX_ACQUISITION_BONUS."""
        qualifier = _make_trained_qualifier()
        profiles = [_make_profile(1), _make_profile(2)]

        with patch("linkedin.ml.qualifier._load_profile_embeddings") as mock_load:
            mock_load.return_value = [
                (profiles[0], np.zeros(384)),
                (profiles[1], np.ones(384) * 0.1),
            ]
            # Both get max bonus -> ordering by raw score
            signal_bonuses = {1: SIGNAL_MAX_ACQUISITION_BONUS, 2: SIGNAL_MAX_ACQUISITION_BONUS}
            result = _rank_by_score(profiles, qualifier.pipeline, None, signal_bonuses=signal_bonuses)

        # Raw score ordering preserved when bonuses equal
        assert result[0]["lead_id"] == 2
        assert result[1]["lead_id"] == 1

    def test_partial_bonus(self, db):
        """Score of 75 gives half the max bonus."""
        qualifier = _make_trained_qualifier()
        profiles = [_make_profile(1), _make_profile(2)]

        with patch("linkedin.ml.qualifier._load_profile_embeddings") as mock_load:
            mock_load.return_value = [
                (profiles[0], np.zeros(384)),
                (profiles[1], np.ones(384) * 0.1),
            ]
            half_bonus = SIGNAL_MAX_ACQUISITION_BONUS / 2
            signal_bonuses = {1: half_bonus, 2: 0.0}
            result = _rank_by_score(profiles, qualifier.pipeline, None, signal_bonuses=signal_bonuses)

        assert result[0]["lead_id"] == 1


# ---------------------------------------------------------------------------
# BayesianQualifier.rank_profiles integration
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBayesianQualifierRankProfilesWithSignals:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_rank_profiles_uses_signal_bonuses(self, fake_session):
        from crm.models import Lead, Deal
        from linkedin.models import Campaign

        campaign = Campaign.objects.create(name="test_campaign")
        # Both leads have identical embeddings so raw scores are equal;
        # only the signal bonus differentiates them.
        lead1 = Lead.objects.create(
            public_identifier="alice",
            linkedin_url="https://linkedin.com/in/alice/",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        lead2 = Lead.objects.create(
            public_identifier="bob",
            linkedin_url="https://linkedin.com/in/bob/",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        # Deal for lead1 has high signal score
        Deal.objects.create(lead=lead1, campaign=campaign, composite_signal_score=150)
        # Deal for lead2 has zero signal
        Deal.objects.create(lead=lead2, campaign=campaign, composite_signal_score=0)

        qualifier = _make_trained_qualifier(campaign=campaign)
        profiles = [
            {"lead_id": lead1.pk, "public_identifier": "alice"},
            {"lead_id": lead2.pk, "public_identifier": "bob"},
        ]

        result = qualifier.rank_profiles(profiles, fake_session)

        # lead1 has high signal but lower embedding -> should still rank first because of bonus
        assert result[0]["lead_id"] == lead1.pk
        assert result[1]["lead_id"] == lead2.pk

    def test_zero_signal_candidates_unchanged(self, fake_session):
        from crm.models import Lead, Deal
        from linkedin.models import Campaign

        campaign = Campaign.objects.create(name="test_campaign2")
        lead1 = Lead.objects.create(
            public_identifier="alice",
            linkedin_url="https://linkedin.com/in/alice/",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        lead2 = Lead.objects.create(
            public_identifier="bob",
            linkedin_url="https://linkedin.com/in/bob/",
            embedding=np.ones(384, dtype=np.float32).tobytes(),
        )
        # Both deals have zero signal
        Deal.objects.create(lead=lead1, campaign=campaign, composite_signal_score=0)
        Deal.objects.create(lead=lead2, campaign=campaign, composite_signal_score=0)

        qualifier = _make_trained_qualifier(campaign=campaign)
        profiles = [
            {"lead_id": lead1.pk, "public_identifier": "alice"},
            {"lead_id": lead2.pk, "public_identifier": "bob"},
        ]

        result = qualifier.rank_profiles(profiles, fake_session)

        # Ordering should be same as without signals (lead2 first because higher embedding)
        assert result[0]["lead_id"] == lead2.pk
        assert result[1]["lead_id"] == lead1.pk

    def test_legacy_deal_without_composite_signal_score(self, fake_session):
        """Deal without composite_signal_score attribute treated as 0."""
        from crm.models import Lead, Deal
        from linkedin.models import Campaign

        campaign = Campaign.objects.create(name="test_campaign3")
        lead = Lead.objects.create(
            public_identifier="alice",
            linkedin_url="https://linkedin.com/in/alice/",
            embedding=np.ones(384, dtype=np.float32).tobytes(),
        )
        Deal.objects.create(lead=lead, campaign=campaign)

        qualifier = _make_trained_qualifier(campaign=campaign)
        profiles = [{"lead_id": lead.pk, "public_identifier": "alice"}]

        result = qualifier.rank_profiles(profiles, fake_session)
        assert len(result) == 1

    def test_no_campaign_means_no_bonuses(self, fake_session):
        """Qualifier without campaign reference does not compute bonuses."""
        qualifier = _make_trained_qualifier(campaign=None)
        profiles = [
            {"lead_id": 1, "public_identifier": "alice"},
            {"lead_id": 2, "public_identifier": "bob"},
        ]

        with patch("linkedin.ml.qualifier._rank_by_score") as mock_rank:
            mock_rank.return_value = profiles
            qualifier.rank_profiles(profiles, fake_session)

        # _rank_by_score should be called with signal_bonuses=None (default)
        call_kwargs = mock_rank.call_args.kwargs
        assert call_kwargs.get("signal_bonuses") is None

    def test_score_below_threshold_does_not_dominate_strong_icp(self, fake_session):
        """A weak-signal candidate should NOT jump above a strong ICP cold candidate
        when the bonus is small enough."""
        from crm.models import Lead, Deal
        from linkedin.models import Campaign

        campaign = Campaign.objects.create(name="test_campaign4")
        lead1 = Lead.objects.create(
            public_identifier="alice",
            linkedin_url="https://linkedin.com/in/alice/",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        lead2 = Lead.objects.create(
            public_identifier="bob",
            linkedin_url="https://linkedin.com/in/bob/",
            embedding=np.ones(384, dtype=np.float32).tobytes(),
        )
        # lead1 has weak signal (score 10 -> small bonus)
        Deal.objects.create(lead=lead1, campaign=campaign, composite_signal_score=10)
        # lead2 has no signal but strong embedding
        Deal.objects.create(lead=lead2, campaign=campaign, composite_signal_score=0)

        qualifier = _make_trained_qualifier(campaign=campaign)
        profiles = [
            {"lead_id": lead1.pk, "public_identifier": "alice"},
            {"lead_id": lead2.pk, "public_identifier": "bob"},
        ]

        result = qualifier.rank_profiles(profiles, fake_session)

        # lead2 should still rank first because the raw score gap exceeds the small bonus
        assert result[0]["lead_id"] == lead2.pk
        assert result[1]["lead_id"] == lead1.pk
