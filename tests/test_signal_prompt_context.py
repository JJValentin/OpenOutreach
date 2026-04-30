"""Tests for signal context in follow-up agent prompts.

TDD: RED first — these tests SHOULD FAIL until build_signal_context is implemented.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from tests.factories import DealFactory, LeadFactory


class TestBuildSignalContext:
    """Tests for build_signal_context helper."""

    def test_returns_none_when_signal_metadata_empty(self, db, fake_session):
        """Signal context is None when deal.signal_metadata is empty dict."""
        from linkedin.agents._signal_context import build_signal_context

        lead = LeadFactory(public_identifier="testlead")
        deal = DealFactory(lead=lead, campaign=fake_session.campaign, signal_metadata={})
        result = build_signal_context(deal)
        assert result is None

    def test_returns_none_when_signals_list_empty(self, db, fake_session):
        """Signal context is None when signals list is empty."""
        from linkedin.agents._signal_context import build_signal_context

        lead = LeadFactory(public_identifier="testlead")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={"composite_score": 0, "signals": []},
        )
        result = build_signal_context(deal)
        assert result is None

    def test_renders_signal_context_block(self, db, fake_session):
        """When signal_metadata has signals, a context block is rendered."""
        from linkedin.agents._signal_context import build_signal_context

        lead = LeadFactory(public_identifier="testlead")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={
                "composite_score": 42,
                "signals": [
                    {
                        "source_kind": "own_post",
                        "source_name": "My Company",
                        "post_excerpt": "Excited to announce our new product launch!",
                        "engagement_type": "reaction",
                        "timestamp": "2026-04-28T10:00:00+00:00",
                        "comment_text": "",
                    }
                ],
            },
        )
        result = build_signal_context(deal)
        assert result is not None
        assert "My Company" in result

    def test_includes_comment_when_present(self, db, fake_session):
        """Comment text is included in the signal block when present."""
        from linkedin.agents._signal_context import build_signal_context

        lead = LeadFactory(public_identifier="testlead")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={
                "composite_score": 42,
                "signals": [
                    {
                        "source_kind": "own_post",
                        "source_name": "My Company",
                        "post_excerpt": "Excited to announce our new product launch!",
                        "engagement_type": "comment",
                        "timestamp": "2026-04-28T10:00:00+00:00",
                        "comment_text": "Great insights! Would love to learn more.",
                    }
                ],
            },
        )
        result = build_signal_context(deal)
        assert result is not None
        assert "Great insights" in result

    def test_limits_to_top_3_signals(self, db, fake_session):
        """Only the top 3 signals are included (metadata already sorted by recency)."""
        from linkedin.agents._signal_context import build_signal_context

        signals = []
        for i in range(5):
            signals.append({
                "source_kind": "own_post",
                "source_name": "Source " + str(i),
                "post_excerpt": "Post number " + str(i),
                "engagement_type": "reaction",
                "timestamp": "2026-04-" + str(28 - i).zfill(2) + "T10:00:00+00:00",
                "comment_text": "",
            })

        lead = LeadFactory(public_identifier="testlead")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={
                "composite_score": 99,
                "signals": signals,
            },
        )
        result = build_signal_context(deal)
        assert result is not None
        assert "Source 0" in result
        assert "Source 1" in result
        assert "Source 2" in result
        assert "Source 3" not in result
        assert "Source 4" not in result


class TestRenderSignalContextInPrompt:
    """Tests for signal context appearing in the rendered follow_up prompt."""

    def _render_prompt(self, deal, mock_messages=None):
        """Helper: render the follow_up system prompt with a deal."""
        if mock_messages is None:
            mock_messages = []

        fake_session = MagicMock()
        fake_session.self_profile = {"first_name": "Test", "last_name": "User"}
        fake_session.django_user.username = "testuser"

        from linkedin.agents.follow_up import _render_system_prompt

        with patch("linkedin.agents.follow_up._load_recent_messages", return_value=mock_messages):
            return _render_system_prompt(fake_session, deal, mock_messages)

    def test_signal_block_present_when_metadata_populated(self, db, fake_session):
        """The rendered prompt includes the signal block when deal has signal_metadata."""
        lead = LeadFactory(public_identifier="alice")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={
                "composite_score": 42,
                "signals": [
                    {
                        "source_kind": "own_post",
                        "source_name": "My Company",
                        "post_excerpt": "Big announcement today!",
                        "engagement_type": "reaction",
                        "timestamp": "2026-04-28T10:00:00+00:00",
                        "comment_text": "",
                    }
                ],
            },
        )
        rendered = self._render_prompt(deal)
        assert "Warm Signal Context" in rendered

    def test_signal_block_absent_when_metadata_empty(self, db, fake_session):
        """The rendered prompt omits the signal block when deal has no signal_metadata."""
        lead = LeadFactory(public_identifier="alice")
        deal = DealFactory(lead=lead, campaign=fake_session.campaign, signal_metadata={})
        rendered = self._render_prompt(deal)
        assert "Warm Signal Context" not in rendered

    def test_all_3_signals_shown_when_available(self, db, fake_session):
        """When 3 signals are in metadata, all 3 appear in the prompt."""
        signals = []
        for i in range(3):
            signals.append({
                "source_kind": "own_post",
                "source_name": "Source " + str(i),
                "post_excerpt": "Post number " + str(i),
                "engagement_type": "reaction",
                "timestamp": "2026-04-" + str(28 - i).zfill(2) + "T10:00:00+00:00",
                "comment_text": "",
            })

        lead = LeadFactory(public_identifier="alice")
        deal = DealFactory(
            lead=lead,
            campaign=fake_session.campaign,
            signal_metadata={
                "composite_score": 99,
                "signals": signals,
            },
        )
        rendered = self._render_prompt(deal)
        assert "Source 0" in rendered
        assert "Source 1" in rendered
        assert "Source 2" in rendered