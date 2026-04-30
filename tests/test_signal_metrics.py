"""
RED phase: Tests for signal metrics counters.
"""
import pytest
from unittest.mock import patch

from linkedin.signals.metrics import get_counters, reset_counters, record_poll


class TestMetricsCounters:
    def test_counters_start_at_zero(self):
        reset_counters()
        assert get_counters() == {
            "polls_run": 0, "signals_created": 0, "signals_updated": 0,
            "rate_limits_hit": 0, "sources_auto_disabled": 0, "account_health_skips": 0,
        }

    def test_record_poll_increments_polls_run(self):
        reset_counters()
        record_poll(signals_created=1)
        counters = get_counters()
        assert counters["polls_run"] == 1
        assert counters["signals_created"] == 1

    def test_record_poll_rate_limited_increments_counter(self):
        reset_counters()
        record_poll(rate_limited=True)
        assert get_counters()["rate_limits_hit"] == 1

    def test_record_poll_source_disabled_increments_counter(self):
        reset_counters()
        record_poll(source_disabled=True)
        assert get_counters()["sources_auto_disabled"] == 1

    def test_record_poll_account_health_skip_increments_counter(self):
        reset_counters()
        record_poll(account_health_skip=True)
        assert get_counters()["account_health_skips"] == 1

    def test_reset_counters_zeros_all(self):
        record_poll(signals_created=5, signals_updated=3, rate_limited=True)
        reset_counters()
        assert get_counters() == {
            "polls_run": 0, "signals_created": 0, "signals_updated": 0,
            "rate_limits_hit": 0, "sources_auto_disabled": 0, "account_health_skips": 0,
        }


class TestAccountHealthHelper:
    def test_is_account_health_ok_default_true(self, db):
        from linkedin.signals.polling import is_account_health_ok
        from linkedin.models import WatchedSource, Campaign
        campaign = Campaign.objects.create(name="Test Campaign")
        source = WatchedSource.objects.create(
            campaign=campaign,
            kind=WatchedSource.Kind.OWN_PROFILE,
            identifier="test",
            display_name="Test",
        )
        result = is_account_health_ok(source)
        assert result is True
