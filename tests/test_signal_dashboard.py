import pytest
from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from linkedin.models import Campaign, Signal, SignalRadarState, WatchedSource
from linkedin.admin import WatchedSourceAdmin, SignalAdmin, SignalRadarStateAdmin
from tests.factories import CampaignFactory, SignalFactory, WatchedSourceFactory


# ── 1. CSV Export Action ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_export_signals_csv_action():
    """SignalAdmin.export_signals_csv returns CSV with expected columns and rows."""
    site = AdminSite()
    admin_instance = SignalAdmin(Signal, site)
    campaign = CampaignFactory.create()

    # Create a watched source first
    ws = WatchedSourceFactory.create(campaign=campaign)

    # Create two signals
    s1 = SignalFactory.create(watched_source=ws, post_urn="urn:li:post:111")
    s2 = SignalFactory.create(watched_source=ws, post_urn="urn:li:post:222")

    queryset = Signal.objects.filter(pk__in=[s1.pk, s2.pk])
    request = RequestFactory().get("/")

    response = admin_instance.export_signals_csv(request, queryset)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment; filename=signals.csv" in response["Content-Disposition"]

    # Parse CSV content
    import csv
    import io
    decoded = response.content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = list(reader)

    assert len(rows) == 2
    assert set(reader.fieldnames) == {
        "profile_urn", "post_urn", "engagement_type", "score",
        "kind", "source_kind", "source_name", "created_at",
    }


# ── 2. poll_now Action ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_poll_now_action_calls_enqueue():
    """WatchedSourceAdmin.poll_now calls enqueue_poll_watched_source for each source."""
    site = AdminSite()
    admin_instance = WatchedSourceAdmin(WatchedSource, site)
    campaign = CampaignFactory.create()
    ws = WatchedSourceFactory.create(campaign=campaign)

    request = RequestFactory().get("/")
    queryset = WatchedSource.objects.filter(pk=ws.pk)

    with patch("linkedin.admin.enqueue_poll_watched_source") as mock_enqueue:
        admin_instance.poll_now(request, queryset)

    mock_enqueue.assert_called_once_with(ws.id)


@pytest.mark.django_db
def test_poll_now_action_multiple_sources():
    """poll_now calls enqueue once per source (idempotent dedupe handled by scheduler)."""
    site = AdminSite()
    admin_instance = WatchedSourceAdmin(WatchedSource, site)
    campaign = CampaignFactory.create()
    ws1 = WatchedSourceFactory.create(campaign=campaign, identifier="id1")
    ws2 = WatchedSourceFactory.create(campaign=campaign, identifier="id2")

    request = RequestFactory().get("/")
    queryset = WatchedSource.objects.filter(pk__in=[ws1.pk, ws2.pk])

    with patch("linkedin.admin.enqueue_poll_watched_source") as mock_enqueue:
        admin_instance.poll_now(request, queryset)

    assert mock_enqueue.call_count == 2
    mock_enqueue.assert_any_call(ws1.id)
    mock_enqueue.assert_any_call(ws2.id)


# ── 3. SignalRadarState Admin is Registered ────────────────────────────────────

def test_signal_radar_state_is_registered():
    """SignalRadarState is registered with the admin site."""
    assert admin.site.is_registered(SignalRadarState)


# ── 4. pause_polling_globally ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_pause_polling_globally_sets_paused_until():
    """pause_polling_globally action sets paused_until to future time."""
    site = AdminSite()
    admin_instance = SignalRadarStateAdmin(SignalRadarState, site)

    state = SignalRadarState.load()
    state.paused_until = None
    state.save()

    request = RequestFactory().get("/")
    queryset = SignalRadarState.objects.all()

    admin_instance.pause_polling_globally(request, queryset)

    state.refresh_from_db()
    assert state.paused_until is not None
    assert state.paused_until > timezone.now()
    # Should be approximately 4 hours from now
    expected = timezone.now() + timedelta(hours=4)
    diff = abs((state.paused_until - expected).total_seconds())
    assert diff < 5  # within 5 seconds


# ── 5. resume_polling_globally ────────────────────────────────────────────────

@pytest.mark.django_db
def test_resume_polling_globally_clears_paused_until():
    """resume_polling_globally action clears paused_until to None."""
    site = AdminSite()
    admin_instance = SignalRadarStateAdmin(SignalRadarState, site)

    state = SignalRadarState.load()
    state.paused_until = timezone.now() + timedelta(hours=2)
    state.save()

    request = RequestFactory().get("/")
    queryset = SignalRadarState.objects.all()

    admin_instance.resume_polling_globally(request, queryset)

    state.refresh_from_db()
    assert state.paused_until is None


# ── 6. Unhealthy Source Banner ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_unhealthy_source_banner_warning():
    """changelist_view shows warning for sources with consecutive_failures >= 1."""
    from django.contrib.auth.models import User
    from django.contrib.messages.storage.fallback import FallbackStorage

    site = AdminSite()
    admin_instance = WatchedSourceAdmin(WatchedSource, site)
    campaign = CampaignFactory.create()

    # Create a healthy source
    healthy = WatchedSourceFactory.create(
        campaign=campaign, consecutive_failures=0, last_error=""
    )
    # Create an unhealthy source
    unhealthy = WatchedSourceFactory.create(
        campaign=campaign,
        display_name="BadCo",
        consecutive_failures=2,
        last_error="Connection timeout after 30s",
    )

    # Build a mock request with messages and user using RequestFactory
    factory = RequestFactory()
    request = factory.get("/admin/linkedin/watchedsource/")
    request.session = {}
    request._messages = FallbackStorage(request)
    # Provide a minimal user with perms needed for admin access
    request.user = User.objects.create_superuser("admin", "a@a.com", "password")

    # Call changelist_view with NO queryset arg (Django uses URL routing internally)
    response = admin_instance.changelist_view(request)

    # Check messages were added
    messages = list(request._messages)
    warning_messages = [m.message for m in messages if m.level == 30]  # WARNING
    assert any(
        "BadCo" in str(m) and "2 consecutive failures" in str(m) and "Connection timeout" in str(m)
        for m in warning_messages
    )

    assert response.status_code == 200