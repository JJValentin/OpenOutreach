"""
linkedin/signals/polling.py — Poll a WatchedSource for new post engagements.

Each poll:
1. Fetches posts from the source (own profile / competitor / influencer).
2. For each post (capped at SIGNAL_MAX_POSTS_PER_POLL), fetches reactors,
   comments, and reposts (each capped at SIGNAL_MAX_ENGAGERS_PER_POST).
3. Upserts Signal rows by (campaign, profile_urn, post_urn, engagement_type).
4. Score = compute_base_score × compute_recency_multiplier.
5. Updates source.last_poll_at.
6. On 429 → sets global pause via SignalRadarState.
7. On error → increments source.consecutive_failures; 3 failures → auto-disable.
8. On success → resets consecutive_failures to 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from django.utils import timezone as dj_timezone

from linkedin import conf as conf_module
from linkedin.api import posts as posts_api
from linkedin.models import Signal, SignalRadarState, WatchedSource
from linkedin.signals import scoring
from linkedin.signals.metrics import record_poll

logger = logging.getLogger(__name__)

# Re-export config values for convenience in patching by tests
SIGNAL_RADAR_ENABLED = conf_module.SIGNAL_RADAR_ENABLED
SIGNAL_MAX_POSTS_PER_POLL = conf_module.SIGNAL_MAX_POSTS_PER_POLL
SIGNAL_MAX_ENGAGERS_PER_POST = conf_module.SIGNAL_MAX_ENGAGERS_PER_POST
SIGNAL_RATE_LIMIT_PAUSE_HOURS = conf_module.SIGNAL_RATE_LIMIT_PAUSE_HOURS

# Number of consecutive failures before auto-disabling a source
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class PollResult:
    posts_seen: int = 0
    engagers_seen: int = 0
    signals_created: int = 0
    signals_updated: int = 0
    capped: bool = False
    errors: list[str] = field(default_factory=list)
    rate_limited: bool = False  # True if a 429 was received


def _is_paused() -> bool:
    """Return True if Signal Radar is globally paused (429 hit)."""
    state = SignalRadarState.load()
    if state.paused_until is None:
        return False
    return state.paused_until > dj_timezone.now()


def _set_pause(hours: int) -> None:
    """Set the global pause window."""
    from linkedin.models import SignalRadarState
    state = SignalRadarState.load()
    state.paused_until = dj_timezone.now() + timedelta(hours=hours)
    state.save(update_fields=["paused_until"])


def _kind_to_source_kind(kind: str) -> str:
    """Map WatchedSource.Kind to the source_kind used in scoring."""
    if kind == WatchedSource.Kind.OWN_PROFILE:
        return "own_post"
    elif kind == WatchedSource.Kind.COMPETITOR_COMPANY:
        return "competitor"
    else:
        return "influencer"


def _parse_iso(ts_str: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string to a datetime (UTC)."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _upsert_signal(
    campaign,
    watched_source,
    profile_urn: str,
    post_urn: str,
    engagement_type: str,
    post_excerpt: str,
    post_author_urn: str,
    post_published_at: datetime | None,
    payload_json: dict,
    now: datetime,
) -> tuple[Signal, bool]:
    """
    Create or update a Signal row.

    Returns (signal, was_created). was_created is True if a row was created,
    False if an existing row was updated.
    """
    source_kind = _kind_to_source_kind(watched_source.kind)

    # Compute score
    base = scoring.compute_base_score(engagement_type, source_kind)
    recency_mult = (
        scoring.compute_recency_multiplier(post_published_at, now)
        if post_published_at
        else 1.0
    )
    score = int(base * recency_mult)

    defaults = {
        "watched_source": watched_source,
        "kind": (
            Signal.Kind.OWN_POST_ENGAGEMENT
            if watched_source.kind == WatchedSource.Kind.OWN_PROFILE
            else (
                Signal.Kind.COMPETITOR_ENGAGEMENT
                if watched_source.kind == WatchedSource.Kind.COMPETITOR_COMPANY
                else Signal.Kind.INFLUENCER_ENGAGEMENT
            )
        ),
        "post_excerpt": post_excerpt,
        "post_author_urn": post_author_urn,
        "post_published_at": post_published_at,
        "payload_json": payload_json,
        "score": score,
    }

    signal, was_created = Signal.objects.update_or_create(
        campaign=campaign,
        profile_urn=profile_urn,
        post_urn=post_urn,
        engagement_type=engagement_type,
        defaults=defaults,
    )
    return signal, was_created


def is_account_health_ok(source: WatchedSource) -> bool:
    """
    Check if the account health is OK for polling.

    Looks for existing OpenOutreach action-budget state on the linkedin_profile.
    LinkedInProfile.can_execute(action_type) checks daily/weekly rate limits via ActionLog.
    If no budget signal is found, defaults to True.
    """
    try:
        linkedin_profile = source.campaign.users.first().linkedin_profile
        if linkedin_profile is None:
            return True
        # Check if any action type is exhausted
        for action_type in ("connect", "follow_up"):
            try:
                if not linkedin_profile.can_execute(action_type):
                    return False
            except KeyError:
                pass  # Action type not in rate limit fields, skip
        return True
    except Exception:
        logger.warning(
            "account_health_check: no budget system found, defaulting to True",
            extra={"source_id": source.id},
        )
        return True


def poll_watched_source(source: WatchedSource, session) -> PollResult:
    """
    Poll a single WatchedSource for new engagement signals.

    Skips if SIGNAL_RADAR_ENABLED is False or source.is_active is False.
    Skips if globally paused (429 was received).
    Skips if account health check fails.
    Caps posts and engagers per post per conf settings.
    Upserts Signal rows by (campaign, profile_urn, post_urn, engagement_type).
    Updates source.last_poll_at on success.
    On 429: sets global pause and returns immediately.
    On error: increments source.consecutive_failures; disables after 3.
    On success: resets source.consecutive_failures to 0.
    """
    result = PollResult()

    if not SIGNAL_RADAR_ENABLED:
        return result

    if not source.is_active:
        return result

    # Account health check
    if not is_account_health_ok(source):
        logger.warning(
            "account_health_skip",
            extra={"source_id": source.id, "reason": "account_health_not_ok"},
        )
        record_poll(account_health_skip=True)
        return result

    if not SIGNAL_RADAR_ENABLED:
        return result

    if not source.is_active:
        return result

    # Check global pause (429-triggered)
    if _is_paused():
        logger.warning("Signal Radar globally paused, skipping poll for %s", source)
        return result

    logger.info(
        "poll_start",
        extra={
            "source_id": source.id,
            "source_kind": source.kind,
            "source_name": source.display_name,
        },
    )

    campaign = source.campaign
    now = datetime.now(timezone.utc)

    try:
        # Determine which posts API to call based on source kind
        if source.kind == WatchedSource.Kind.OWN_PROFILE:
            posts = posts_api.list_own_profile_posts(
                session, since_days=30, limit=SIGNAL_MAX_POSTS_PER_POLL,
            )
        elif source.kind == WatchedSource.Kind.COMPETITOR_COMPANY:
            posts = posts_api.list_company_posts(
                session, company_slug=source.identifier, limit=SIGNAL_MAX_POSTS_PER_POLL,
            )
        else:
            posts = posts_api.list_profile_posts(
                session, public_id=source.identifier, limit=SIGNAL_MAX_POSTS_PER_POLL,
            )

        posts = list(posts)[:SIGNAL_MAX_POSTS_PER_POLL]
        result.posts_seen = len(posts)
        if len(posts) >= SIGNAL_MAX_POSTS_PER_POLL:
            result.capped = True

        for post in posts:
            post_urn = post.get("post_urn", "")
            post_excerpt = post.get("post_excerpt", "")
            author_urn = post.get("author_urn", "")
            published_at = _parse_iso(post.get("published_at", ""))

            engagers_this_post = 0

            # Reactions
            reactors = posts_api.list_post_reactors(
                session, post_urn=post_urn, limit=SIGNAL_MAX_ENGAGERS_PER_POST,
            )
            for reactor in reactors:
                if engagers_this_post >= SIGNAL_MAX_ENGAGERS_PER_POST:
                    result.capped = True
                    break
                profile_urn = reactor.get("profile_urn", "")
                reaction_type = reactor.get("reaction_type", "LIKE")
                signal, created = _upsert_signal(
                    campaign=campaign,
                    watched_source=source,
                    profile_urn=profile_urn,
                    post_urn=post_urn,
                    engagement_type=Signal.EngagementType.REACTION,
                    post_excerpt=post_excerpt,
                    post_author_urn=author_urn,
                    post_published_at=published_at,
                    payload_json={"reaction_type": reaction_type},
                    now=now,
                )
                if created:
                    result.signals_created += 1
                else:
                    result.signals_updated += 1
                result.engagers_seen += 1
                engagers_this_post += 1

            # Comments
            comments = posts_api.list_post_comments(
                session, post_urn=post_urn, limit=SIGNAL_MAX_ENGAGERS_PER_POST,
            )
            for comment in comments:
                if engagers_this_post >= SIGNAL_MAX_ENGAGERS_PER_POST:
                    result.capped = True
                    break
                profile_urn = comment.get("profile_urn", "")
                signal, created = _upsert_signal(
                    campaign=campaign,
                    watched_source=source,
                    profile_urn=profile_urn,
                    post_urn=post_urn,
                    engagement_type=Signal.EngagementType.COMMENT,
                    post_excerpt=post_excerpt,
                    post_author_urn=author_urn,
                    post_published_at=published_at,
                    payload_json={"comment_text": comment.get("comment_text", "")},
                    now=now,
                )
                if created:
                    result.signals_created += 1
                else:
                    result.signals_updated += 1
                result.engagers_seen += 1
                engagers_this_post += 1

            # Reposts
            reposts = posts_api.list_post_reposts(
                session, post_urn=post_urn, limit=SIGNAL_MAX_ENGAGERS_PER_POST,
            )
            for repost in reposts:
                if engagers_this_post >= SIGNAL_MAX_ENGAGERS_PER_POST:
                    result.capped = True
                    break
                profile_urn = repost.get("profile_urn", "")
                signal, created = _upsert_signal(
                    campaign=campaign,
                    watched_source=source,
                    profile_urn=profile_urn,
                    post_urn=post_urn,
                    engagement_type=Signal.EngagementType.REPOST,
                    post_excerpt=post_excerpt,
                    post_author_urn=author_urn,
                    post_published_at=published_at,
                    payload_json={},
                    now=now,
                )
                if created:
                    result.signals_created += 1
                else:
                    result.signals_updated += 1
                result.engagers_seen += 1
                engagers_this_post += 1

        # Update last_poll_at and reset failure counter on success
        source.last_poll_at = dj_timezone.now()
        source.consecutive_failures = 0
        source.last_error = ""
        source.save(update_fields=["last_poll_at", "consecutive_failures", "last_error"])

        logger.warning(
            "poll_complete",
            extra={
                "source_id": source.id,
                "posts_seen": result.posts_seen,
                "engagers_seen": result.engagers_seen,
                "signals_created": result.signals_created,
                "signals_updated": result.signals_updated,
                "capped": result.capped,
            },
        )
        record_poll(
            signals_created=result.signals_created,
            signals_updated=result.signals_updated,
        )

        return result

    except Exception as exc:
        error_str = str(exc)
        is_429 = "429" in error_str

        if is_429:
            # Set global pause
            _set_pause(SIGNAL_RATE_LIMIT_PAUSE_HOURS)
            result.rate_limited = True
            logger.warning(
                "rate_limit_hit",
                extra={"source_id": source.id, "pause_hours": SIGNAL_RATE_LIMIT_PAUSE_HOURS},
            )
            record_poll(rate_limited=True)
            # Do NOT increment consecutive_failures for 429 (global pause handles it)
            raise

        # Increment consecutive failures
        source.consecutive_failures += 1
        source.last_error = error_str

        if source.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            source.is_active = False
            logger.warning(
                "source_auto_disabled",
                extra={"source_id": source.id, "consecutive_failures": source.consecutive_failures},
            )
            record_poll(source_disabled=True)

        source.save(update_fields=["consecutive_failures", "last_error", "is_active"])
        result.errors.append(error_str)
        raise
