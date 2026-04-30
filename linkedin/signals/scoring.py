"""Signal scoring functions for LinkedIn engagement signals."""
from datetime import datetime, timezone
from typing import Tuple


def compute_base_score(engagement_type: str, source_kind: str) -> int:
    """Compute base score for an engagement.

    Args:
        engagement_type: One of "comment", "reaction", "repost"
        source_kind: One of "own_post", "competitor", "influencer"

    Returns:
        Base score integer
    """
    scores = {
        ("comment", "own_post"): 100,
        ("reaction", "own_post"): 85,
        ("repost", "own_post"): 70,
        ("comment", "competitor"): 75,
        ("reaction", "competitor"): 55,
        ("repost", "competitor"): 70,
        ("comment", "influencer"): 70,
        ("reaction", "influencer"): 55,
        ("repost", "influencer"): 70,
    }
    return scores.get((engagement_type, source_kind), 0)


def compute_recency_multiplier(event_time: datetime, now: datetime = None) -> float:
    """Compute recency multiplier based on age of event.

    Args:
        event_time: When the engagement occurred
        now: Current time for testing (defaults to datetime.now(timezone.utc))

    Returns:
        Multiplier: 1.0 (≤3d), 0.75 (≤10d), 0.5 (≤25d), 0.2 (>25d)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    age_days = (now - event_time).total_seconds() / 86400

    if age_days <= 3:
        return 1.0
    elif age_days <= 10:
        return 0.75
    elif age_days <= 25:
        return 0.5
    else:
        return 0.2


def compute_frequency_bonus(count_within_30d: int) -> int:
    """Compute frequency bonus based on event count.

    Args:
        count_within_30d: Number of events in the 30-day window

    Returns:
        Bonus: 0 (1 event), 10 (2), 15 (3), 20 (≥10), capped at 20
    """
    if count_within_30d <= 1:
        return 0
    elif count_within_30d == 2:
        return 10
    elif count_within_30d == 3:
        return 15
    elif count_within_30d >= 10:
        return 20
    else:
        return 15  # 4-9 events get 15


def cap_composite_score(value: int) -> int:
    """Cap composite score at 150.

    Args:
        value: Raw composite score

    Returns:
        Capped score, minimum 0
    """
    if value < 0:
        return 0
    return min(value, 150)


def compute_composite_score(
    events: Tuple[Tuple[str, str, datetime], ...], now: datetime = None
) -> int:
    """Compute composite signal score from a collection of engagements.

    Formula: sum(base_score * recency_multiplier) + frequency_bonus

    Args:
        events: Iterable of (engagement_type, source_kind, event_time) tuples
        now: Current time for testing

    Returns:
        Composite score, capped at 150
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not events:
        return 0

    total = 0.0
    for engagement_type, source_kind, event_time in events:
        base = compute_base_score(engagement_type, source_kind)
        multiplier = compute_recency_multiplier(event_time, now=now)
        total += base * multiplier

    frequency_bonus = compute_frequency_bonus(len(events))
    total += frequency_bonus

    return cap_composite_score(int(total))
