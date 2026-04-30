"""
RED phase: Tests for structured logging in polling.py.
These tests define the expected structured log output before implementation.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from linkedin.models import Campaign, WatchedSource
from linkedin.signals.polling import poll_watched_source


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


@pytest.fixture
def campaign_obj(db):
    return Campaign.objects.create(name="Test Campaign")


@pytest.fixture
def own_profile_source(db, campaign_obj):
    return WatchedSource.objects.create(
        campaign=campaign_obj,
        kind=WatchedSource.Kind.OWN_PROFILE,
        identifier="testuser",
        display_name="Test User",
        is_active=True,
        cadence_minutes=30,
    )


class TestStructuredLogging:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_poll_start_logs_source_info(self, own_profile_source, campaign_obj, fake_session, caplog):
        post = make_mock_post("urn:li:fs_post:LOG001", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts, \
             patch("linkedin.signals.polling.is_account_health_ok", return_value=True):
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            with caplog.at_level("INFO", logger="linkedin.signals.polling"):
                poll_watched_source(own_profile_source, fake_session)

        poll_start_records = [r for r in caplog.records if r.message == "poll_start"]
        assert len(poll_start_records) == 1
        rec = poll_start_records[0]
        assert rec.source_id == own_profile_source.id
        assert rec.source_kind == "own_profile"
        assert rec.source_name == "Test User"

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_poll_complete_logs_result_counts(self, own_profile_source, campaign_obj, fake_session, caplog):
        post = make_mock_post("urn:li:fs_post:LOG002", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:100", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts, \
             patch("linkedin.signals.polling.is_account_health_ok", return_value=True):
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            with caplog.at_level("INFO", logger="linkedin.signals.polling"):
                poll_watched_source(own_profile_source, fake_session)

        poll_complete_records = [r for r in caplog.records if r.message == "poll_complete"]
        assert len(poll_complete_records) == 1
        rec = poll_complete_records[0]
        assert rec.posts_seen == 1
        assert rec.engagers_seen == 1
        assert rec.signals_created == 1
        assert rec.signals_updated == 0
        assert rec.capped is False

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_poll_complete_logs_capped_flag(self, own_profile_source, fake_session, caplog):
        posts = [make_mock_post(f"urn:li:fs_post:CAP{i}", idx=i) for i in range(1, 6)]
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts, \
             patch("linkedin.signals.polling.is_account_health_ok", return_value=True):
            mock_posts.return_value = posts[:4]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            with patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 3):
                with caplog.at_level("INFO", logger="linkedin.signals.polling"):
                    poll_watched_source(own_profile_source, fake_session)

        poll_complete_records = [r for r in caplog.records if r.message == "poll_complete"]
        assert len(poll_complete_records) == 1
        assert poll_complete_records[0].capped is True


class TestRateLimitLogging:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    def test_rate_limit_hit_logs_warning(self, own_profile_source, fake_session, caplog):
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.is_account_health_ok", return_value=True):
            mock_posts.side_effect = Exception("429 Too Many Requests")
            with caplog.at_level("WARNING", logger="linkedin.signals.polling"):
                with pytest.raises(Exception, match="429"):
                    poll_watched_source(own_profile_source, fake_session)

        rate_limit_records = [r for r in caplog.records if r.message == "rate_limit_hit"]
        assert len(rate_limit_records) == 1
        rec = rate_limit_records[0]
        assert rec.source_id == own_profile_source.id
        assert hasattr(rec, "pause_hours")


class TestSourceAutoDisableLogging:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_source_auto_disabled_logs_warning(self, own_profile_source, fake_session, caplog):
        own_profile_source.consecutive_failures = 2
        own_profile_source.save(update_fields=["consecutive_failures"])

        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.is_account_health_ok", return_value=True):
            mock_posts.side_effect = Exception("Some error")
            with caplog.at_level("WARNING", logger="linkedin.signals.polling"):
                with pytest.raises(Exception):
                    poll_watched_source(own_profile_source, fake_session)

        auto_disabled_records = [r for r in caplog.records if r.message == "source_auto_disabled"]
        assert len(auto_disabled_records) == 1
        rec = auto_disabled_records[0]
        assert rec.source_id == own_profile_source.id
        assert rec.consecutive_failures == 3


class TestAccountHealthSkipLogging:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    def test_account_health_skip_logs_warning(self, own_profile_source, fake_session, caplog):
        with patch("linkedin.signals.polling.is_account_health_ok", return_value=False):
            with caplog.at_level("WARNING", logger="linkedin.signals.polling"):
                result = poll_watched_source(own_profile_source, fake_session)

        assert result.posts_seen == 0
        health_skip_records = [r for r in caplog.records if r.message == "account_health_skip"]
        assert len(health_skip_records) == 1
        rec = health_skip_records[0]
        assert rec.source_id == own_profile_source.id
        assert rec.reason == "account_health_not_ok"
