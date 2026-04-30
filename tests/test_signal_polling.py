"""
RED phase: Tests for linkedin/signals/polling.py and linkedin/tasks/poll_signals.py
These tests define the expected polling + deduplication behavior before implementation.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, ANY

from django.utils import timezone as dj_timezone

from linkedin.models import Campaign, LinkedInProfile, Signal, Task, WatchedSource
from linkedin.signals.polling import PollResult, poll_watched_source
from linkedin.tasks.poll_signals import (
    handle_poll_own_posts,
    handle_poll_watched_source,
)
from linkedin.tasks.scheduler import (
    enqueue_poll_own_posts,
    enqueue_poll_watched_source,
)


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


def make_mock_reposts(profile_urns):
    return [
        {"profile_urn": urn, "public_identifier": f"reposter{i}"}
        for (i, urn) in enumerate(profile_urns, start=1)
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


@pytest.fixture
def competitor_source(db, campaign_obj):
    return WatchedSource.objects.create(
        campaign=campaign_obj,
        kind=WatchedSource.Kind.COMPETITOR_COMPANY,
        identifier="acme-corp",
        display_name="Acme Corp",
        is_active=True,
        cadence_minutes=120,
    )


@pytest.fixture
def inactive_source(db, campaign_obj):
    return WatchedSource.objects.create(
        campaign=campaign_obj,
        kind=WatchedSource.Kind.INFLUENCER_PROFILE,
        identifier="inactive-influencer",
        display_name="Inactive Influencer",
        is_active=False,
        cadence_minutes=60,
    )


class TestPollResultDataclass:
    def test_poll_result_fields(self):
        result = PollResult(
            posts_seen=5, engagers_seen=20, signals_created=10,
            signals_updated=3, capped=False, errors=[],
        )
        assert result.posts_seen == 5
        assert result.engagers_seen == 20
        assert result.signals_created == 10
        assert result.signals_updated == 3
        assert result.capped is False
        assert result.errors == []


class TestPollWatchedSourceBasic:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", False)
    def test_returns_early_when_disabled(self, own_profile_source, fake_session):
        result = poll_watched_source(own_profile_source, fake_session)
        assert result.posts_seen == 0
        assert result.engagers_seen == 0
        assert result.signals_created == 0
        assert result.signals_updated == 0
        assert result.errors == []

    def test_returns_early_when_source_inactive(self, inactive_source, fake_session):
        result = poll_watched_source(inactive_source, fake_session)
        assert result.posts_seen == 0
        assert result.engagers_seen == 0
        assert result.signals_created == 0
        assert result.signals_updated == 0


class TestPollCreatesSignals:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_one_reactor_creates_signal(self, own_profile_source, campaign_obj, fake_session):
        post = make_mock_post("urn:li:fs_post:123", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:100", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            result = poll_watched_source(own_profile_source, fake_session)
        assert result.posts_seen == 1
        assert result.engagers_seen == 1
        assert result.signals_created == 1
        signal = Signal.objects.get(
            campaign=campaign_obj, profile_urn="urn:li:fs_member:100",
            post_urn="urn:li:fs_post:123",
            engagement_type=Signal.EngagementType.REACTION,
        )
        assert signal.kind == Signal.Kind.OWN_POST_ENGAGEMENT
        assert signal.score > 0
        assert signal.post_published_at is not None

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_one_reactor_plus_one_commenter_creates_two_signals(self, own_profile_source, campaign_obj, fake_session):
        post = make_mock_post("urn:li:fs_post:456", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:200", "LIKE")])
        comments = make_mock_comments([("urn:li:fs_member:201", "Great post!")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments_fn, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments_fn.return_value = comments
            mock_reposts.return_value = []
            result = poll_watched_source(own_profile_source, fake_session)
        assert result.posts_seen == 1
        assert result.engagers_seen == 2
        assert result.signals_created == 2
        signals = Signal.objects.filter(
            campaign=campaign_obj, post_urn="urn:li:fs_post:456"
        ).order_by("engagement_type")
        assert signals.count() == 2
        reaction_signal = signals.get(engagement_type=Signal.EngagementType.REACTION)
        assert reaction_signal.profile_urn == "urn:li:fs_member:200"
        comment_signal = signals.get(engagement_type=Signal.EngagementType.COMMENT)
        assert comment_signal.profile_urn == "urn:li:fs_member:201"


class TestPollDeduplication:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_rerun_does_not_create_duplicates_updates_timestamp(self, own_profile_source, campaign_obj, fake_session):
        post = make_mock_post("urn:li:fs_post:789", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:300", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            result1 = poll_watched_source(own_profile_source, fake_session)
            assert result1.signals_created == 1
            assert result1.signals_updated == 0
            result2 = poll_watched_source(own_profile_source, fake_session)
            assert result2.signals_created == 0
            assert result2.signals_updated == 1
            assert Signal.objects.filter(
                campaign=campaign_obj, profile_urn="urn:li:fs_member:300",
                post_urn="urn:li:fs_post:789",
            ).count() == 1


class TestPostAndEngagerCaps:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 2)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 3)
    def test_post_cap_respected(self, own_profile_source, fake_session):
        posts = [make_mock_post(f"urn:li:fs_post:p{i}", idx=i) for i in range(1, 6)]
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = posts
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            result = poll_watched_source(own_profile_source, fake_session)
        assert result.posts_seen == 2
        assert mock_reactors.call_count == 2

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 2)
    def test_engager_cap_per_post_respected(self, own_profile_source, fake_session):
        post = make_mock_post("urn:li:fs_post:cap_test", idx=1)
        many_reactors = make_mock_reactors([(f"urn:li:fs_member:{i}", "LIKE") for i in range(10)])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = many_reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            result = poll_watched_source(own_profile_source, fake_session)
        assert result.engagers_seen == 2
        assert result.signals_created == 2


class TestSourceLastPollAt:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_last_poll_at_is_updated(self, own_profile_source, fake_session):
        assert own_profile_source.last_poll_at is None
        post = make_mock_post("urn:li:fs_post:LP001", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            poll_watched_source(own_profile_source, fake_session)
        own_profile_source.refresh_from_db()
        assert own_profile_source.last_poll_at is not None


class TestSignalScore:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_score_is_computed_base_times_recency(self, own_profile_source, campaign_obj, fake_session):
        post = make_mock_post("urn:li:fs_post:SC001", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:400", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            poll_watched_source(own_profile_source, fake_session)
        signal = Signal.objects.get(
            campaign=campaign_obj, profile_urn="urn:li:fs_member:400",
            post_urn="urn:li:fs_post:SC001",
        )
        # base 85 (reaction on own_post) x 1.0 (fresh) = 85
        assert 80 <= signal.score <= 90


class TestSchedulerEnqueue:
    def test_enqueue_poll_watched_source_creates_task(self, own_profile_source):
        enqueue_poll_watched_source(own_profile_source.id, delay_seconds=0)
        assert Task.objects.filter(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
        ).exists()

    def test_enqueue_poll_watched_source_deduplicates(self, own_profile_source):
        enqueue_poll_watched_source(own_profile_source.id, delay_seconds=0)
        enqueue_poll_watched_source(own_profile_source.id, delay_seconds=0)
        assert Task.objects.filter(
            task_type=Task.TaskType.POLL_WATCHED_SOURCE,
            status=Task.Status.PENDING,
        ).count() == 1

    def test_enqueue_poll_own_posts_creates_task(self, campaign_obj):
        enqueue_poll_own_posts(campaign_obj.id)
        assert Task.objects.filter(
            task_type=Task.TaskType.POLL_OWN_POSTS,
            status=Task.Status.PENDING,
        ).exists()

    def test_enqueue_poll_own_posts_deduplicates(self, campaign_obj):
        enqueue_poll_own_posts(campaign_obj.id)
        enqueue_poll_own_posts(campaign_obj.id)
        assert Task.objects.filter(
            task_type=Task.TaskType.POLL_OWN_POSTS,
            status=Task.Status.PENDING,
        ).count() == 1


class TestHandlers:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_handle_poll_watched_source_runs_poll(self, own_profile_source, fake_session):
        post = make_mock_post("urn:li:fs_post:H001", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            task = Task.objects.create(
                task_type=Task.TaskType.POLL_WATCHED_SOURCE,
                status=Task.Status.PENDING,
                scheduled_at=dj_timezone.now(),
                payload={"watched_source_id": own_profile_source.id},
            )
            handle_poll_watched_source(task, fake_session, qualifiers={})
        own_profile_source.refresh_from_db()
        assert own_profile_source.last_poll_at is not None

    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_handle_poll_own_posts_runs_poll(self, campaign_obj, fake_session):
        src = WatchedSource.objects.create(
            campaign=campaign_obj, kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="testuser", display_name="Test User", is_active=True,
        )
        post = make_mock_post("urn:li:fs_post:H002", idx=1)
        with patch("linkedin.signals.polling.posts_api.list_own_profile_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = []
            mock_comments.return_value = []
            mock_reposts.return_value = []
            task = Task.objects.create(
                task_type=Task.TaskType.POLL_OWN_POSTS,
                status=Task.Status.PENDING,
                scheduled_at=dj_timezone.now(),
                payload={"campaign_id": campaign_obj.id},
            )
            handle_poll_own_posts(task, fake_session, qualifiers={})
        src.refresh_from_db()
        assert src.last_poll_at is not None


class TestSignalsScopedToCampaign:
    @patch("linkedin.signals.polling.SIGNAL_RADAR_ENABLED", True)
    @patch("linkedin.signals.polling.SIGNAL_MAX_POSTS_PER_POLL", 20)
    @patch("linkedin.signals.polling.SIGNAL_MAX_ENGAGERS_PER_POST", 500)
    def test_signals_belong_to_source_campaign(self, competitor_source, campaign_obj, fake_session):
        post = make_mock_post("urn:li:fs_post:SCOPE001", idx=1)
        reactors = make_mock_reactors([("urn:li:fs_member:500", "LIKE")])
        with patch("linkedin.signals.polling.posts_api.list_company_posts") as mock_posts, \
             patch("linkedin.signals.polling.posts_api.list_post_reactors") as mock_reactors, \
             patch("linkedin.signals.polling.posts_api.list_post_comments") as mock_comments, \
             patch("linkedin.signals.polling.posts_api.list_post_reposts") as mock_reposts:
            mock_posts.return_value = [post]
            mock_reactors.return_value = reactors
            mock_comments.return_value = []
            mock_reposts.return_value = []
            poll_watched_source(competitor_source, fake_session)
        signal = Signal.objects.get(
            profile_urn="urn:li:fs_member:500", post_urn="urn:li:fs_post:SCOPE001",
        )
        assert signal.campaign == campaign_obj
        assert signal.kind == Signal.Kind.COMPETITOR_ENGAGEMENT