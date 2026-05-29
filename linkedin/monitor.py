from __future__ import annotations

import json
import os
import subprocess
from datetime import timezone as datetime_timezone
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Min
from django.shortcuts import render
from django.utils import timezone

from chat.models import ChatMessage
from crm.models import Deal, Lead
from linkedin.models import ActionLog, Campaign, Signal, Task, WatchedSource

ACTIVE_CAMPAIGN_NAME = "Social Money Models"
PIPELINE_ORDER = [
    "Qualified",
    "Ready to Connect",
    "Pending",
    "Connected",
    "Completed",
    "Failed",
]
OUTREACH_TASK_TYPES = [
    Task.TaskType.CONNECT,
    Task.TaskType.CHECK_PENDING,
    Task.TaskType.FOLLOW_UP,
    Task.TaskType.INJECT_SIGNAL_PROFILES,
    Task.TaskType.RECOMPUTE_SIGNAL_SCORES,
]
SIGNAL_TASK_TYPES = [
    Task.TaskType.POLL_WATCHED_SOURCE,
    Task.TaskType.POLL_OWN_POSTS,
]
GUARDIAN_DIR = Path("/home/clawdbot/.hermes/openoutreach")
OUTREACH_STATE_FILE = GUARDIAN_DIR / "outreach_state.json"
SIGNAL_STATE_FILE = GUARDIAN_DIR / "state.json"
OUTREACH_CAP_ENV = "OPENOUTREACH_OUTREACH_DAILY_CAP"
DEFAULT_OUTREACH_DAILY_CAP = 20


def _systemctl_user_env() -> dict:
    env = os.environ.copy()
    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def _systemctl_user_status(unit: str) -> dict:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            env=_systemctl_user_env(),
        )
        status = (result.stdout or result.stderr or "unknown").strip()
    except Exception as exc:  # pragma: no cover - monitor must degrade gracefully
        status = f"unknown ({exc.__class__.__name__})"
    return {
        "unit": unit,
        "status": status,
        "healthy": status == "active",
    }


def _daemon_status() -> dict:
    """Legacy daemon status kept for rollback/debug visibility.

    OpenOutreach now normally runs through bounded guardian timers, so this is
    no longer the primary health indicator shown by the monitor.
    """
    return _systemctl_user_status("openoutreach-rundaemon.service")


def _load_json_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc)


def _age_label(dt) -> str:
    if not dt:
        return "never"
    delta = timezone.now() - dt
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m ago"
    days = hours // 24
    return f"{days}d ago"


def _guardian_state_summary(path: Path) -> dict:
    state = _load_json_state(path)
    last_run_at = _parse_dt(state.get("last_run_at"))
    return {
        "path": str(path),
        "state": state,
        "last_run_at": last_run_at,
        "last_run_age": _age_label(last_run_at),
        "last_status": state.get("last_status") or "unknown",
        "last_error": state.get("last_error"),
    }


def _outreach_daily_cap() -> int:
    try:
        return int(os.environ.get(OUTREACH_CAP_ENV, DEFAULT_OUTREACH_DAILY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_OUTREACH_DAILY_CAP


def _queue_summary(campaign=None) -> dict:
    now = timezone.now()
    outreach = Task.objects.pending().filter(task_type__in=OUTREACH_TASK_TYPES)
    signal = Task.objects.pending().filter(task_type__in=SIGNAL_TASK_TYPES)
    campaign_outreach = outreach
    campaign_signal = signal
    if campaign:
        campaign_outreach = campaign_outreach.filter(payload__campaign_id=campaign.pk)
        campaign_signal = campaign_signal.filter(payload__campaign_id=campaign.pk)

    def summarize(qs):
        due = qs.filter(scheduled_at__lte=now)
        return {
            "total": qs.count(),
            "due": due.count(),
            "next_due_at": qs.aggregate(next_due=Min("scheduled_at"))["next_due"],
        }

    return {
        "outreach": summarize(outreach),
        "signal": summarize(signal),
        "campaign_outreach": summarize(campaign_outreach),
        "campaign_signal": summarize(campaign_signal),
    }


def _guardian_status(campaign=None) -> dict:
    outreach = _guardian_state_summary(OUTREACH_STATE_FILE)
    signal = _guardian_state_summary(SIGNAL_STATE_FILE)
    outreach_timer = _systemctl_user_status("openoutreach-outreach-guardian.timer")
    signal_timer = _systemctl_user_status("openoutreach-signal-radar-guardian.timer")
    queue = _queue_summary(campaign=campaign)
    daily_cap = _outreach_daily_cap()
    writes_today = int(outreach["state"].get("writes_today") or 0)
    outreach_ok = outreach_timer["healthy"] and outreach["last_status"] in {"success", "skipped"}
    signal_ok = signal_timer["healthy"] and signal["last_status"] in {"success", "skipped"}
    return {
        "outreach": outreach,
        "signal": signal,
        "outreach_timer": outreach_timer,
        "signal_timer": signal_timer,
        "queue": queue,
        "daily_cap": daily_cap,
        "writes_today": writes_today,
        "writes_remaining": max(daily_cap - writes_today, 0),
        "healthy": outreach_ok and signal_ok,
        "outreach_ok": outreach_ok,
        "signal_ok": signal_ok,
    }


def _selected_campaign(request):
    campaigns = list(Campaign.objects.order_by("-id"))
    requested = request.GET.get("campaign")
    if requested and requested.isdigit():
        campaign = Campaign.objects.filter(pk=int(requested)).first()
        if campaign:
            return campaign, campaigns
    campaign = Campaign.objects.filter(name=ACTIVE_CAMPAIGN_NAME).first()
    if not campaign and campaigns:
        campaign = campaigns[0]
    return campaign, campaigns


def _today_window():
    now = timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now, start


def _duration(started_at):
    if not started_at:
        return ""
    delta = timezone.now() - started_at
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _task_label(task):
    if not task:
        return None
    label = task.task_type.replace("_", " ")
    public_id = (task.payload or {}).get("public_id")
    if public_id:
        label = f"{label} · {public_id}"
    return {
        "id": task.id,
        "label": label,
        "status": task.status,
        "started_at": task.started_at,
        "duration": _duration(task.started_at),
        "payload": task.payload,
    }


@staff_member_required
def monitor(request):
    campaign, campaigns = _selected_campaign(request)
    now, today_start = _today_window()

    campaign_tasks = Task.objects.all()
    campaign_deals = Deal.objects.select_related("lead", "campaign")
    watched_sources = WatchedSource.objects.select_related("campaign")
    signals = Signal.objects.select_related("watched_source")
    action_logs = ActionLog.objects.select_related("campaign", "linkedin_profile")

    if campaign:
        campaign_tasks = campaign_tasks.filter(payload__campaign_id=campaign.pk)
        campaign_deals = campaign_deals.filter(campaign=campaign)
        watched_sources = watched_sources.filter(campaign=campaign)
        signals = signals.filter(campaign=campaign)
        action_logs = action_logs.filter(campaign=campaign)

    raw_counts = {
        row["state"]: row["n"]
        for row in campaign_deals.values("state").annotate(n=Count("id"))
    }
    pipeline_rows = [{"state": state, "count": raw_counts.get(state, 0)} for state in PIPELINE_ORDER]
    other_states = sorted(set(raw_counts) - set(PIPELINE_ORDER))
    pipeline_rows.extend({"state": state, "count": raw_counts[state]} for state in other_states)

    task_counts = {
        row["status"]: row["n"]
        for row in campaign_tasks.values("status").annotate(n=Count("id"))
    }
    task_type_counts = {
        row["task_type"]: row["n"]
        for row in campaign_tasks.values("task_type").annotate(n=Count("id"))
    }

    running_task = campaign_tasks.filter(status=Task.Status.RUNNING).order_by("started_at").first()
    pending_tasks = campaign_tasks.filter(status=Task.Status.PENDING).order_by("scheduled_at")[:8]
    recent_tasks = campaign_tasks.order_by("-created_at")[:12]
    recent_deals = campaign_deals.order_by("-update_date")[:50]

    lead_ct = ContentType.objects.get(app_label="crm", model="lead")
    lead_ids = list(campaign_deals.values_list("lead_id", flat=True))
    messages = ChatMessage.objects.filter(content_type=lead_ct, object_id__in=lead_ids)
    outgoing_today = messages.filter(is_outgoing=True, creation_date__gte=today_start).count()
    outgoing_recent = messages.filter(is_outgoing=True).order_by("-creation_date")[:10]

    today_deals = campaign_deals.filter(update_date__gte=today_start)
    today_actions = action_logs.filter(created_at__gte=today_start)
    today_signals = signals.filter(created_at__gte=today_start)

    connect_actions_today = today_actions.filter(action_type=ActionLog.ActionType.CONNECT).count()
    follow_up_actions_today = today_actions.filter(action_type=ActionLog.ActionType.FOLLOW_UP).count()
    failures_today = campaign_tasks.filter(status=Task.Status.FAILED, completed_at__gte=today_start).count()
    guardian = _guardian_status(campaign=campaign)

    digest_lines = []
    if campaign:
        digest_lines.append(f"Monitoring {campaign.name}.")
    digest_lines.append(
        f"Today: {today_deals.count()} profile/deal updates, {connect_actions_today} connection actions, "
        f"{outgoing_today} outgoing follow-up messages, {today_signals.count()} signals, {failures_today} failed tasks."
    )
    connected_count = raw_counts.get("Connected", 0)
    pending_count = raw_counts.get("Pending", 0)
    completed_count = raw_counts.get("Completed", 0)
    digest_lines.append(
        f"Pipeline now: {pending_count} pending connections, {connected_count} connected, {completed_count} completed."
    )
    digest_lines.append(
        f"Guardians: outreach {guardian['outreach']['last_status']} {guardian['outreach']['last_run_age']}; "
        f"signal radar {guardian['signal']['last_status']} {guardian['signal']['last_run_age']}."
    )
    if follow_up_actions_today == 0:
        digest_lines.append("No automated follow-up messages have been recorded today.")

    context = {
        "now": now,
        "campaign": campaign,
        "campaigns": campaigns,
        "daemon": _daemon_status(),
        "guardian": guardian,
        "running_task": _task_label(running_task),
        "pipeline_rows": pipeline_rows,
        "task_counts": task_counts,
        "task_type_counts": task_type_counts,
        "pending_tasks": pending_tasks,
        "recent_tasks": recent_tasks,
        "recent_deals": recent_deals,
        "watched_sources": watched_sources.order_by("kind", "display_name")[:20],
        "signals_today_count": today_signals.count(),
        "outgoing_today": outgoing_today,
        "connect_actions_today": connect_actions_today,
        "follow_up_actions_today": follow_up_actions_today,
        "failures_today": failures_today,
        "digest_lines": digest_lines,
        "pending_count": pending_count,
        "connected_count": connected_count,
        "completed_count": completed_count,
        "task_admin_base": "/admin/linkedin/task/",
        "deal_admin_base": "/admin/crm/deal/",
        "lead_admin_base": "/admin/crm/lead/",
    }
    return render(request, "linkedin/monitor.html", context)
