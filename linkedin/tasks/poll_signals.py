"""
linkedin/tasks/poll_signals.py — Task handlers for poll_own_posts and poll_watched_source.

handle_poll_own_posts:
  - Finds all OWN_PROFILE WatchedSources for the campaign.
  - Polls each one via poll_watched_source().
  - On success, reschedules with cadence ± 25% jitter.
  - On error, increments consecutive_failures + logs last_error.

handle_poll_watched_source:
  - Loads the WatchedSource from payload.watched_source_id.
  - Calls poll_watched_source().
  - On success, reschedules with cadence ± 25% jitter.
  - On error, increments consecutive_failures + logs last_error.
  - Disabled sources (is_active=False) are NOT rescheduled.

Both handlers mark the task COMPLETED or FAILED.
"""
from __future__ import annotations

import logging
import random

from django.utils import timezone

from linkedin.models import Task, WatchedSource
from linkedin.signals.polling import poll_watched_source

logger = logging.getLogger(__name__)


def _reschedule(source: WatchedSource) -> None:
    """Reschedule the next poll with configurable jitter and stochastic skip.

    Jitter formula: multiplier = 1 - (jitter_percent / 200) + (jitter_percent / 100) * random.random()
    This produces a uniform distribution centered on cadence, ranging +/-jitter_percent/2 of cadence.

    With probability SIGNAL_POLL_SKIP_PROBABILITY, delay is doubled to simulate a human skip.
    """
    from linkedin import conf
    from linkedin.tasks.scheduler import enqueue_poll_watched_source

    jitter_percent = conf.SIGNAL_POLL_JITTER_PERCENT
    skip_prob = conf.SIGNAL_POLL_SKIP_PROBABILITY

    multiplier = 1 - (jitter_percent / 200) + (jitter_percent / 100) * random.random()
    delay_seconds = source.cadence_minutes * 60 * multiplier

    if random.random() < skip_prob:
        delay_seconds *= 2

    enqueue_poll_watched_source(source.id, delay_seconds=delay_seconds)

def handle_poll_watched_source(task: Task, session, qualifiers: dict) -> None:
    """Handle a POLL_WATCHED_SOURCE task."""
    watched_source_id = task.payload.get("watched_source_id")
    if not watched_source_id:
        logger.error("POLL_WATCHED_SOURCE task missing watched_source_id: %s", task)
        return

    try:
        source = WatchedSource.objects.get(id=watched_source_id)
    except WatchedSource.DoesNotExist:
        logger.error("WatchedSource %s not found", watched_source_id)
        return

    # Do not reschedule if source is inactive (was auto-disabled)
    if not source.is_active:
        logger.info("WatchedSource %s is inactive, skipping poll", source)
        return

    try:
        result = poll_watched_source(source, session)

        # Reset failure counter on success; record successful poll time
        source.consecutive_failures = 0
        source.last_error = ""
        source.last_successful_poll_at = timezone.now()
        source.save(update_fields=["consecutive_failures", "last_error", "last_successful_poll_at"])

        # Reschedule with jitter
        _reschedule(source)

        logger.info(
            "Poll watched source %s: posts=%d engagers=%d created=%d updated=%d",
            source.display_name or source.identifier,
            result.posts_seen, result.engagers_seen,
            result.signals_created, result.signals_updated,
        )

        for err in result.errors:
            logger.warning("Poll error for %s: %s", source.display_name, err)

    except Exception as exc:
        logger.exception("Poll watched source %s failed: %s", source, exc)
        # Re-fetch to get latest state (may have been auto-disabled in polling.py)
        source.refresh_from_db()

        if not source.is_active:
            logger.info("WatchedSource %s was auto-disabled after consecutive failures, not rescheduling", source)
        else:
            # Increment failures if not already disabled by polling.py
            source.consecutive_failures += 1
            source.last_error = str(exc)
            source.save(update_fields=["consecutive_failures", "last_error"])
            _reschedule(source)
        raise


def handle_poll_own_posts(task: Task, session, qualifiers: dict) -> None:
    """Handle a POLL_OWN_POSTS task — poll all OWN_PROFILE sources for a campaign."""
    campaign_id = task.payload.get("campaign_id")
    if not campaign_id:
        logger.error("POLL_OWN_POSTS task missing campaign_id: %s", task)
        return

    sources = WatchedSource.objects.filter(
        campaign_id=campaign_id,
        kind=WatchedSource.Kind.OWN_PROFILE,
        is_active=True,
    )

    total_created = 0
    total_updated = 0

    for source in sources:
        try:
            result = poll_watched_source(source, session)
            total_created += result.signals_created
            total_updated += result.signals_updated

            # Reset failure counter on success; record successful poll time
            source.consecutive_failures = 0
            source.last_error = ""
            source.last_successful_poll_at = timezone.now()
            source.save(update_fields=["consecutive_failures", "last_error", "last_successful_poll_at"])

            # Reschedule with jitter
            _reschedule(source)

        except Exception as exc:
            logger.exception("Poll own posts source %s failed: %s", source, exc)
            source.refresh_from_db()

            if not source.is_active:
                logger.info("WatchedSource %s was auto-disabled, not rescheduling", source)
            else:
                source.consecutive_failures += 1
                source.last_error = str(exc)
                source.save(update_fields=["consecutive_failures", "last_error"])
            # Continue with other sources

    logger.info(
        "Poll own posts campaign %d: %d sources, created=%d updated=%d",
        campaign_id, sources.count(), total_created, total_updated,
    )
