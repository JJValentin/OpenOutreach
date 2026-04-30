"""Signal metadata builder for LinkedIn engagement signals."""
from datetime import datetime, timezone
from typing import Iterable, Union

from django.db.models import QuerySet

from linkedin.models import Signal, WatchedSource
from linkedin.signals.scoring import compute_composite_score


# Mapping from Signal.kind to source_kind string used by scoring module
_SIGNAL_KIND_TO_SOURCE_KIND = {
    Signal.Kind.OWN_POST_ENGAGEMENT: "own_post",
    Signal.Kind.COMPETITOR_ENGAGEMENT: "competitor",
    Signal.Kind.INFLUENCER_ENGAGEMENT: "influencer",
}

# Mapping from WatchedSource.kind to source_kind string used by scoring module
_WATCHED_SOURCE_KIND_TO_SOURCE_KIND = {
    WatchedSource.Kind.OWN_PROFILE: "own_post",
    WatchedSource.Kind.COMPETITOR_COMPANY: "competitor",
    WatchedSource.Kind.INFLUENCER_PROFILE: "influencer",
}


def _get_source_kind(signal: Signal) -> str:
    """Derive source_kind string for a signal.

    Prefers watched_source.kind if available, otherwise falls back to
    Signal.kind mapping.
    """
    if signal.watched_source_id is not None:
        return _WATCHED_SOURCE_KIND_TO_SOURCE_KIND.get(
            signal.watched_source.kind, "own_post"
        )
    return _SIGNAL_KIND_TO_SOURCE_KIND.get(signal.kind, "own_post")


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length characters."""
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _build_events_for_scoring(signals: list[Signal]) -> list[tuple]:
    """Build events tuple list for compute_composite_score.

    Args:
        signals: List of Signal objects

    Returns:
        List of (engagement_type, source_kind, event_time) tuples
    """
    events = []
    for sig in signals:
        source_kind = _get_source_kind(sig)
        # Use post_published_at if available, otherwise created_at
        event_time = sig.post_published_at if sig.post_published_at else sig.created_at
        # Ensure timezone-aware datetime
        if event_time and event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        events.append((sig.engagement_type, source_kind, event_time))
    return events


def build_signal_metadata(
    signals_qs_or_iterable: Union[QuerySet, Iterable],
    *,
    max_signals: int = 5,
) -> dict:
    """Build a JSON-serializable metadata dict from signals.

    Args:
        signals_qs_or_iterable: QuerySet or iterable of Signal objects
        max_signals: Maximum number of signals to include (default 5)

    Returns:
        dict with keys:
            - composite_score (int)
            - signals (list of compact signal dicts)
    """
    # Materialize to list if it's a queryset
    if isinstance(signals_qs_or_iterable, QuerySet):
        signals_list = list(signals_qs_or_iterable)
    else:
        signals_list = list(signals_qs_or_iterable)

    if not signals_list:
        return {"composite_score": 0, "signals": []}

    # Sort: most recent first (-created_at), ties broken by score descending,
    # then by id for deterministic ordering
    sorted_signals = sorted(
        signals_list,
        key=lambda s: (-s.created_at.timestamp() if s.created_at else 0, -s.score, s.id),
    )

    # Take top max_signals
    top_signals = sorted_signals[:max_signals]

    # Build events for composite score calculation
    events = _build_events_for_scoring(top_signals)
    composite_score = compute_composite_score(events)

    # Build compact signal dicts
    compact_signals = []
    for sig in top_signals:
        comment_text = sig.payload_json.get("comment_text", "") if sig.payload_json else ""
        if comment_text:
            comment_text = _truncate(comment_text, 2000)

        compact_sig = {
            "source_kind": _get_source_kind(sig),
            "source_name": (
                sig.watched_source.display_name or sig.watched_source.identifier
                if sig.watched_source_id else ""
            ),
            "post_urn": sig.post_urn,
            "post_excerpt": _truncate(sig.post_excerpt, 240),
            "engagement_type": sig.engagement_type,
            "timestamp": (
                sig.created_at.isoformat()
                if sig.created_at.tzinfo
                else sig.created_at.replace(tzinfo=timezone.utc).isoformat()
            ),
        }
        if comment_text:
            compact_sig["comment_text"] = comment_text

        compact_signals.append(compact_sig)

    return {
        "composite_score": composite_score,
        "signals": compact_signals,
    }


def update_deal_signal_metadata(deal, signals_iterable: Iterable) -> None:
    """Update a Deal's signal metadata from an iterable of signals.

    Args:
        deal: Deal model instance
        signals_iterable: Iterable of Signal objects
    """
    metadata = build_signal_metadata(signals_iterable)
    deal.signal_metadata = metadata
    deal.composite_signal_score = metadata["composite_score"]
    deal.signal_sourced = True
    deal.save(update_fields=["signal_metadata", "composite_signal_score", "signal_sourced"])
