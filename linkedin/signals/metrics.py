"""
Metrics counters for signal polling operations.
"""
from __future__ import annotations

__counters = {
    "polls_run": 0,
    "signals_created": 0,
    "signals_updated": 0,
    "rate_limits_hit": 0,
    "sources_auto_disabled": 0,
    "account_health_skips": 0,
}


def record_poll(
    signals_created: int = 0,
    signals_updated: int = 0,
    rate_limited: bool = False,
    source_disabled: bool = False,
    account_health_skip: bool = False,
) -> None:
    """Increment counters after a poll run."""
    __counters["polls_run"] += 1
    __counters["signals_created"] += signals_created
    __counters["signals_updated"] += signals_updated
    if rate_limited:
        __counters["rate_limits_hit"] += 1
    if source_disabled:
        __counters["sources_auto_disabled"] += 1
    if account_health_skip:
        __counters["account_health_skips"] += 1


def get_counters() -> dict:
    """Return copy of current counters."""
    return dict(__counters)


def reset_counters() -> None:
    """Reset all counters to zero."""
    for key in __counters:
        __counters[key] = 0
