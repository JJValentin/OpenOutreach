from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone
from linkedin.exceptions import AuthenticationError
from linkedin.models import Signal, Task, WatchedSource
from linkedin.tasks.poll_signals import handle_poll_own_posts, handle_poll_watched_source
from linkedin.tasks.scheduler import reconcile_signal_radar_tasks


WORKER_VERSION = "1.0.0"
SIGNAL_RADAR_TYPES = (
    Task.TaskType.POLL_WATCHED_SOURCE,
    Task.TaskType.POLL_OWN_POSTS,
)
SIGNAL_RADAR_HANDLERS = {
    Task.TaskType.POLL_WATCHED_SOURCE: handle_poll_watched_source,
    Task.TaskType.POLL_OWN_POSTS: handle_poll_own_posts,
}
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


class Command(BaseCommand):
    help = "Run one Signal Radar worker tick and emit exactly one JSON object."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            default="signal_radar",
            help="Worker kind to run. Only 'signal_radar' is supported.",
        )
        parser.add_argument(
            "--max-runtime-seconds",
            type=int,
            default=600,
            help="Maximum wall-clock runtime for this one-shot worker.",
        )

    def handle(self, *args, **options):
        self._ensure_runtime_environment()
        started_at = timezone.now()
        result = self._base_result(started_at)

        if options["kind"] != "signal_radar":
            result["completed_at"] = self._iso(timezone.now())
            result["errors"].append(
                {"type": "UNSUPPORTED_KIND", "message": options["kind"]}
            )
            self._write_json(result)
            sys.exit(2)

        session = None
        try:
            if not self._ensure_cdp_available():
                result["cdp_auth_valid"] = False
                result["completed_at"] = self._iso(timezone.now())
                result["errors"].append(
                    {"type": "CDP_UNAVAILABLE", "message": "CDP health check failed"}
                )
                self._update_signal_queue_health(result)
                result["next_due_at"] = self._next_due_at()
                self._write_json(result)
                sys.exit(2)

            self._reconcile_signal_radar_queue(result)
            session = self._create_session()
            # Attach sync Playwright before qualifier/LLM setup can enter an
            # asyncio loop. Playwright refuses to start its Sync API while a
            # loop is running in the current thread.
            session.ensure_browser()
            qualifiers = self._build_qualifiers(session)
            exit_code = self._drain_signal_radar_tasks(
                session=session,
                qualifiers=qualifiers,
                result=result,
                deadline=time.monotonic() + options["max_runtime_seconds"],
            )
        except AuthenticationError as exc:
            result["cdp_auth_valid"] = False
            result["errors"].append({"type": "AUTH_REQUIRED", "message": str(exc)})
            exit_code = 1
        except Exception as exc:
            result["errors"].append({"type": "SETUP_ERROR", "message": str(exc)})
            exit_code = 3
        finally:
            if session is not None:
                session.close()

        exit_code = max(exit_code, self._update_signal_queue_health(result))
        result["next_due_at"] = self._next_due_at()
        result["completed_at"] = self._iso(timezone.now())
        self._write_json(result)
        if exit_code != 0:
            sys.exit(exit_code)

    def _base_result(self, started_at):
        return {
            "run_id": str(uuid.uuid4()),
            "started_at": self._iso(started_at),
            "completed_at": None,
            "tasks_processed": 0,
            "tasks_succeeded": 0,
            "tasks_failed": 0,
            "signals_created": 0,
            "signals_updated": 0,
            "signal_sources_active": 0,
            "signal_sources_invalid": 0,
            "signal_sources_normalized": 0,
            "signal_tasks_created": 0,
            "signal_tasks_pending": 0,
            "signal_queue_health": "unknown",
            "errors": [],
            "cdp_auth_valid": True,
            "next_due_at": None,
            "worker_version": WORKER_VERSION,
        }

    def _ensure_runtime_environment(self):
        os.environ.setdefault("DISPLAY", ":99")
        os.environ.setdefault("HEADLESS", "true")
        os.environ.setdefault("OPENOUTREACH_CDP_URL", "http://127.0.0.1:9222")

    def _ensure_cdp_available(self) -> bool:
        health = SCRIPTS_DIR / "cdp-health-check.sh"
        recovery = SCRIPTS_DIR / "cdp-recovery.sh"

        if self._run_script(health).returncode == 0:
            return True
        if self._run_script(recovery).returncode != 0:
            return False
        return self._run_script(health).returncode == 0

    def _run_script(self, path: Path):
        return subprocess.run(
            [str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )

    def _create_session(self):
        from linkedin.management.commands.rundaemon import Command as RunDaemonCommand

        return RunDaemonCommand()._create_session()

    def _build_qualifiers(self, session):
        from linkedin.conf import CAMPAIGN_CONFIG, ENABLE_FREEMIUM_KIT
        from linkedin.daemon import _build_qualifiers
        from linkedin.ml.hub import fetch_kit
        from linkedin.setup.freemium import import_freemium_campaign, seed_profiles

        kit = fetch_kit() if ENABLE_FREEMIUM_KIT else None
        if kit:
            freemium_campaign = import_freemium_campaign(kit["config"])
            if freemium_campaign:
                previous_campaign = getattr(session, "campaign", None)
                session.campaign = freemium_campaign
                seed_profiles(session, kit["config"])
                session.campaign = previous_campaign

        return _build_qualifiers(
            session.campaigns,
            CAMPAIGN_CONFIG,
            kit_model=kit["model"] if kit else None,
        )

    def _drain_signal_radar_tasks(self, session, qualifiers, result, deadline):
        exit_code = 0
        while True:
            if time.monotonic() >= deadline:
                result["errors"].append(
                    {"type": "TIMEOUT", "message": "max runtime exceeded"}
                )
                return 3

            task = self._claim_next_signal_task()
            if task is None:
                return exit_code

            task.mark_running()
            result["tasks_processed"] += 1
            before_count = Signal.objects.count()
            handler = SIGNAL_RADAR_HANDLERS.get(task.task_type)

            try:
                if handler is None:
                    raise RuntimeError(f"No handler for task type {task.task_type}")
                handler(task, session, qualifiers)
                task.refresh_from_db()
                if task.status != Task.Status.COMPLETED:
                    task.mark_completed()
                result["tasks_succeeded"] += 1
                result["signals_created"] += max(Signal.objects.count() - before_count, 0)
            except AuthenticationError as exc:
                task.mark_failed()
                result["tasks_failed"] += 1
                result["errors"].append({"type": "AUTH_REQUIRED", "message": str(exc)})
                exit_code = max(exit_code, 1)
            except Exception as exc:
                task.mark_failed()
                result["tasks_failed"] += 1
                result["errors"].append(
                    {
                        "type": "HANDLER_EXCEPTION",
                        "task_id": task.id,
                        "task_type": task.task_type,
                        "message": str(exc),
                    }
                )
                exit_code = max(exit_code, 3)

    def _claim_next_signal_task(self):
        return (
            Task.objects.pending()
            .filter(task_type__in=SIGNAL_RADAR_TYPES, scheduled_at__lte=timezone.now())
            .first()
        )

    def _reconcile_signal_radar_queue(self, result):
        summary = reconcile_signal_radar_tasks()
        result["signal_sources_active"] = summary["active_sources"]
        result["signal_sources_invalid"] = summary["invalid_active_sources"]
        result["signal_sources_normalized"] = summary["sources_normalized"]
        result["signal_tasks_created"] = summary["tasks_created"]

    def _update_signal_queue_health(self, result) -> int:
        active_count = WatchedSource.objects.filter(is_active=True).count()
        pending_count = Task.objects.pending().filter(task_type__in=SIGNAL_RADAR_TYPES).count()
        invalid_count = (
            WatchedSource.objects.filter(is_active=True)
            .exclude(kind__in=[choice.value for choice in WatchedSource.Kind])
            .count()
        )

        result["signal_sources_active"] = active_count
        result["signal_sources_invalid"] = invalid_count
        result["signal_tasks_pending"] = pending_count

        if invalid_count:
            result["signal_queue_health"] = "invalid_sources"
            result["errors"].append(
                {
                    "type": "SIGNAL_SOURCE_INVALID",
                    "message": f"{invalid_count} active watched source(s) have invalid kind values",
                }
            )
            return 3

        if active_count and pending_count == 0:
            result["signal_queue_health"] = "empty_but_sources"
            result["errors"].append(
                {
                    "type": "SIGNAL_QUEUE_EMPTY",
                    "message": "Signal Radar has active watched sources but no pending poll tasks",
                }
            )
            return 3

        result["signal_queue_health"] = "ok" if active_count else "no_active_sources"
        return 0

    def _next_due_at(self):
        task = Task.objects.pending().filter(task_type__in=SIGNAL_RADAR_TYPES).first()
        if task is None:
            return None
        return self._iso(task.scheduled_at)

    def _write_json(self, result):
        self.stdout.write(json.dumps(result, separators=(",", ":")), ending="")

    def _iso(self, dt):
        if dt is None:
            return None
        return dt.isoformat()
