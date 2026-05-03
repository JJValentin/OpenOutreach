"""
Unit tests for _reschedule() jitter distribution and skip probability.
Spec: signal-radar-jitter-config

Tests verify:
- Jitter multipliers fall within (1 - jitter_percent/100) to (1 + jitter_percent/100) bounds
- Mean of samples is within 2% of expected cadence
- Skip probability fraction is within 1 percentage point of SIGNAL_POLL_SKIP_PROBABILITY
"""
from __future__ import annotations

import statistics
from unittest.mock import MagicMock, patch

import pytest
import django
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openoutreach.settings")
django.setup()

from django.test import override_settings


def _call_reschedule_delay(cadence_minutes: int, n_samples: int = 10_000) -> list[float]:
    """
    Call _reschedule logic directly by importing and patching enqueue_poll_watched_source.
    Returns list of delay_seconds values.
    """
    from linkedin.tasks.poll_signals import _reschedule

    delays = []
    source = MagicMock()
    source.id = 1
    source.cadence_minutes = cadence_minutes

    with patch("linkedin.tasks.scheduler.enqueue_poll_watched_source") as mock_enqueue:
        for _ in range(n_samples):
            _reschedule(source)
            if mock_enqueue.call_args:
                _, kwargs = mock_enqueue.call_args
                delays.append(kwargs.get("delay_seconds", 0))
        return delays


@override_settings(
    SIGNAL_POLL_JITTER_PERCENT=25,
    SIGNAL_POLL_SKIP_PROBABILITY=0.07,
)
def test_jitter_bounds():
    """All jitter multipliers must be within (1 - jitter/100) to (1 + jitter/100)."""
    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60
    jitter_percent = 25

    low = cadence_seconds * (1 - jitter_percent / 100)
    high = cadence_seconds * (1 + jitter_percent / 100)

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    assert len(delays) == 10_000, f"Expected 10000 samples, got {len(delays)}"

    # Account for skip doubles: the high bound doubles for 7% of samples
    # So actual max is cadence_seconds * (1 + jitter/100) * 2
    non_skipped = [d for d in delays if d <= high * 1.01]
    # At minimum 92% of delays must be within normal bounds
    assert len(non_skipped) >= 9000, (
        f"Too many samples outside normal jitter bounds: {len(delays) - len(non_skipped)} / {len(delays)}"
    )

    for d in non_skipped:
        assert low <= d <= high * 1.01, (
            f"Delay {d:.1f}s out of range [{low:.1f}, {high:.1f}]"
        )


@override_settings(
    SIGNAL_POLL_JITTER_PERCENT=25,
    SIGNAL_POLL_SKIP_PROBABILITY=0.07,
)
def test_jitter_mean():
    """Mean of samples (non-skipped) should be within 2% of cadence."""
    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60
    jitter_percent = 25
    high = cadence_seconds * (1 + jitter_percent / 100)

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    # Exclude skipped samples (those doubled)
    normal = [d for d in delays if d <= high * 1.01]
    mean = statistics.mean(normal)
    assert abs(mean - cadence_seconds) / cadence_seconds < 0.02, (
        f"Mean {mean:.1f}s deviates >2% from cadence {cadence_seconds}s"
    )


@override_settings(
    SIGNAL_POLL_JITTER_PERCENT=25,
    SIGNAL_POLL_SKIP_PROBABILITY=0.07,
)
def test_skip_probability():
    """Skip fraction should be within 1 percentage point of 0.07."""
    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60
    jitter_percent = 25
    high = cadence_seconds * (1 + jitter_percent / 100)

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    doubled = [d for d in delays if d > high * 1.01]
    skip_fraction = len(doubled) / len(delays)

    assert 0.06 <= skip_fraction <= 0.08, (
        f"Skip fraction {skip_fraction:.4f} not in [0.06, 0.08]"
    )


@override_settings(
    SIGNAL_POLL_JITTER_PERCENT=0,
    SIGNAL_POLL_SKIP_PROBABILITY=0.0,
)
def test_zero_jitter_deterministic():
    """With SIGNAL_POLL_JITTER_PERCENT=0 and no skip, all delays are exactly cadence."""
    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60

    delays = _call_reschedule_delay(cadence_minutes, 100)
    for d in delays:
        assert abs(d - cadence_seconds) < 0.001, (
            f"Expected deterministic {cadence_seconds}s, got {d}"
        )