"""
Unit tests for _reschedule() jitter distribution and skip probability.
Spec: signal-radar-jitter-config

Tests verify:
- Jitter config constants exist in linkedin.conf with correct types/values
- Jitter multipliers fall within the tight +/-jitter_percent/2 bounds
- Mean of non-skipped samples is within 2% of expected cadence
- Skip probability fraction is within 1 percentage point of configured value
"""
from __future__ import annotations

import statistics
from unittest.mock import MagicMock, patch

import pytest


def _call_reschedule_delay(
    cadence_minutes: int, n_samples: int = 10_000
) -> list:
    """
    Call _reschedule N times and collect delay_seconds values.
    Patches enqueue_poll_watched_source at the scheduler module level.
    """
    from linkedin.tasks.poll_signals import _reschedule

    delays = []
    source = MagicMock()
    source.id = 1
    source.cadence_minutes = cadence_minutes

    with patch(
        "linkedin.tasks.scheduler.enqueue_poll_watched_source"
    ) as mock_enqueue:
        for _ in range(n_samples):
            mock_enqueue.reset_mock()
            _reschedule(source)
            if mock_enqueue.call_args:
                _, kwargs = mock_enqueue.call_args
                delays.append(kwargs.get("delay_seconds", 0))
    return delays


def test_conf_constants_exist():
    """linkedin.conf must define SIGNAL_POLL_JITTER_PERCENT and SIGNAL_POLL_SKIP_PROBABILITY."""
    from linkedin import conf

    assert hasattr(conf, "SIGNAL_POLL_JITTER_PERCENT"), (
        "linkedin.conf missing SIGNAL_POLL_JITTER_PERCENT"
    )
    assert hasattr(conf, "SIGNAL_POLL_SKIP_PROBABILITY"), (
        "linkedin.conf missing SIGNAL_POLL_SKIP_PROBABILITY"
    )
    assert isinstance(conf.SIGNAL_POLL_JITTER_PERCENT, int), (
        "Expected int for SIGNAL_POLL_JITTER_PERCENT, got {}".format(
            type(conf.SIGNAL_POLL_JITTER_PERCENT)
        )
    )
    assert isinstance(conf.SIGNAL_POLL_SKIP_PROBABILITY, float), (
        "Expected float for SIGNAL_POLL_SKIP_PROBABILITY, got {}".format(
            type(conf.SIGNAL_POLL_SKIP_PROBABILITY)
        )
    )
    assert conf.SIGNAL_POLL_JITTER_PERCENT == 25
    assert conf.SIGNAL_POLL_SKIP_PROBABILITY == 0.07


def test_jitter_bounds(monkeypatch):
    """Jitter multipliers must fall within tight +/-jitter_percent/2 bounds."""
    jitter_percent = 25
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_JITTER_PERCENT", jitter_percent)
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_SKIP_PROBABILITY", 0.0)

    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60

    # Tight bounds: +/-12.5% at jitter_percent=25
    low = cadence_seconds * (1 - jitter_percent / 200)
    high = cadence_seconds * (1 + jitter_percent / 200)

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    assert len(delays) == 10_000, "Expected 10000 samples, got {}".format(len(delays))

    for d in delays:
        assert low <= d <= high + 0.001, (
            "Delay {:.3f}s out of tight jitter bounds [{:.1f}, {:.1f}]".format(d, low, high)
        )


def test_jitter_mean(monkeypatch):
    """Mean of samples should be within 2% of cadence."""
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_JITTER_PERCENT", 25)
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_SKIP_PROBABILITY", 0.0)

    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    mean = statistics.mean(delays)
    assert abs(mean - cadence_seconds) / cadence_seconds < 0.02, (
        "Mean {:.1f}s deviates >2% from cadence {}s".format(mean, cadence_seconds)
    )


def test_skip_probability(monkeypatch):
    """Skip fraction should be within 1 percentage point of configured value."""
    jitter_percent = 25
    skip_prob = 0.07
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_JITTER_PERCENT", jitter_percent)
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_SKIP_PROBABILITY", skip_prob)

    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60
    high_normal = cadence_seconds * (1 + jitter_percent / 200)

    delays = _call_reschedule_delay(cadence_minutes, 10_000)
    doubled = [d for d in delays if d > high_normal * 1.001]
    actual_skip_fraction = len(doubled) / len(delays)

    assert 0.06 <= actual_skip_fraction <= 0.08, (
        "Skip fraction {:.4f} not in [0.06, 0.08] (expected ~{})".format(
            actual_skip_fraction, skip_prob
        )
    )


def test_zero_jitter_deterministic(monkeypatch):
    """With jitter=0 and skip_prob=0, all delays equal cadence exactly."""
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_JITTER_PERCENT", 0)
    monkeypatch.setattr("linkedin.conf.SIGNAL_POLL_SKIP_PROBABILITY", 0.0)

    cadence_minutes = 120
    cadence_seconds = cadence_minutes * 60

    delays = _call_reschedule_delay(cadence_minutes, 100)
    for d in delays:
        assert abs(d - cadence_seconds) < 0.001, (
            "Expected deterministic {}s, got {}".format(cadence_seconds, d)
        )
