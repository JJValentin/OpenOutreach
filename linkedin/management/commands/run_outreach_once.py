from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone
from linkedin.exceptions import AuthenticationError
from linkedin.models import Campaign, Task
from linkedin.tasks.check_pending import handle_check_pending
from linkedin.tasks.connect import handle_connect
from linkedin.tasks.follow_up import handle_follow_up
from linkedin.tasks.inject_signal_profiles import handle_inject_signal_profiles
from linkedin.tasks.recompute_signal_scores import handle_recompute_signal_scores


WORKER_VERSION = "1.0.0"
OUTREACH_TYPES = (
    Task.TaskType.CONNECT,
    Task.TaskType.CHECK_PENDING,
    Task.TaskType.FOLLOW_UP,
    Task.TaskType.INJECT_SIGNAL_PROFILES,
    Task.TaskType.RECOMPUTE_SIGNAL_SCORES,
)


def handle_inject_signal_profiles_task(task, session, qualifiers):
    campaign = getattr(session, "campaign", None)
    if campaign is None:
        raise RuntimeError("inject_signal_profiles requires task payload campaign_id")
    handle_inject_signal_profiles(campaign)


OUTREACH_HANDLERS = {
    Task.TaskType.CONNECT: handle_connect,
    Task.TaskType.CHECK_PENDING: handle_check_pending,
    Task.TaskType.FOLLOW_UP: handle_follow_up,
    Task.TaskType.INJECT_SIGNAL_PROFILES: handle_inject_signal_profiles_task,
    Task.TaskType.RECOMPUTE_SIGNAL_SCORES: handle_recompute_signal_scores,
}
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


class Command(BaseCommand):
    help = "Run one bounded OpenOutreach outreach worker tick and emit exactly one JSON object."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-runtime-seconds",
            type=int,
            default=300,
            help="Maximum wall-clock runtime for this one-shot worker.",
        )
        parser.add_argument(
            "--max-tasks",
            type=int,
            default=1,
            help="Maximum outreach tasks to process in this invocation.",
        )
        parser.add_argument(
            "--allow-linkedin-writes",
            action="store_true",
            help="Required to process outreach tasks that may send LinkedIn actions.",
        )

    def handle(self, *args, **options):
        self._ensure_runtime_environment()
        started_at = timezone.now()
        result = self._base_result(started_at)

        if options["max_tasks"] < 1:
            result["completed_at"] = self._iso(timezone.now())
            result["errors"].append(
                {"type": "INVALID_ARGUMENT", "message": "--max-tasks must be >= 1"}
            )
            result["next_due_at"] = self._next_due_at()
            self._write_json(result)
            sys.exit(2)

        if not options["allow_linkedin_writes"]:
            result["completed_at"] = self._iso(timezone.now())
            result["errors"].append(
                {
                    "type": "EXTERNAL_WRITE_APPROVAL_REQUIRED",
                    "message": "pass --allow-linkedin-writes only after explicit operator approval",
                }
            )
            result["next_due_at"] = self._next_due_at()
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
                result["next_due_at"] = self._next_due_at()
                self._write_json(result)
                sys.exit(2)

            session = self._create_session()
            qualifiers = self._build_qualifiers(session)
            self._reconcile_task_queue(session)
            exit_code = self._drain_outreach_tasks(
                session=session,
                qualifiers=qualifiers,
                result=result,
                deadline=time.monotonic() + options["max_runtime_seconds"],
                max_tasks=options["max_tasks"],
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
                try:
                    self._reconcile_task_queue(session)
                finally:
                    session.close()
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

    def _drain_outreach_tasks(self, session, qualifiers, result, deadline, max_tasks):
        exit_code = 0
        while result["tasks_processed"] < max_tasks:
            if time.monotonic() >= deadline:
                result["errors"].append(
                    {"type": "TIMEOUT", "message": "max runtime exceeded"}
                )
                return 3

            task = self._claim_next_outreach_task()
            if task is None:
                return exit_code

            task.mark_running()
            result["tasks_processed"] += 1
            handler = OUTREACH_HANDLERS.get(task.task_type)

            try:
                self._set_session_campaign_for_task(session, task)
                if handler is None:
                    raise RuntimeError(f"No handler for task type {task.task_type}")
                from linkedin import daemon as daemon_module

                previous_timeout = daemon_module.TASK_WATCHDOG_SECONDS.get(task.task_type)
                remaining_seconds = max(1, int(deadline - time.monotonic()) - 5)
                daemon_module.TASK_WATCHDOG_SECONDS[task.task_type] = min(
                    previous_timeout or remaining_seconds,
                    remaining_seconds,
                )
                try:
                    daemon_module.run_task_with_watchdog(handler, task, session, qualifiers)
                finally:
                    if previous_timeout is None:
                        daemon_module.TASK_WATCHDOG_SECONDS.pop(task.task_type, None)
                    else:
                        daemon_module.TASK_WATCHDOG_SECONDS[task.task_type] = previous_timeout
                task.refresh_from_db()
                if task.status != Task.Status.COMPLETED:
                    task.mark_completed()
                result["tasks_succeeded"] += 1
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

        return exit_code


    def _set_session_campaign_for_task(self, session, task):
        campaign_id = (task.payload or {}).get("campaign_id")
        if campaign_id is None:
            return
        campaign = Campaign.objects.filter(pk=campaign_id).first()
        if campaign is None:
            raise RuntimeError(f"Campaign {campaign_id} not found")
        session.campaign = campaign

    def _reconcile_task_queue(self, session):
        from linkedin.tasks.scheduler import reconcile

        reconcile(session)

    def _claim_next_outreach_task(self):
        return (
            Task.objects.pending()
            .filter(task_type__in=OUTREACH_TYPES, scheduled_at__lte=timezone.now())
            .first()
        )

    def _next_due_at(self):
        task = Task.objects.pending().filter(task_type__in=OUTREACH_TYPES).first()
        if task is None:
            return None
        return self._iso(task.scheduled_at)

    def _write_json(self, result):
        self.stdout.write(json.dumps(result, separators=(",", ":")), ending="")

    def _iso(self, dt):
        if dt is None:
            return None
        return dt.isoformat()
