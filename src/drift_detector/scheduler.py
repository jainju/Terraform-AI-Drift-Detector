"""Scheduled drift scanning using APScheduler."""

from __future__ import annotations

import logging
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class DriftScheduler:
    """Schedule periodic drift scans using cron expressions."""

    def __init__(self):
        self._scheduler = BackgroundScheduler()
        self._job_id = "drift_scan"
        self._is_running = False

    def schedule(
        self,
        scan_fn: Callable[[], Any],
        cron_expression: str,
        job_id: str | None = None,
    ) -> None:
        """Schedule a drift scan with a cron expression.

        Args:
            scan_fn: Callable that performs the drift scan.
            cron_expression: Cron expression (minute hour day month day_of_week).
            job_id: Optional job identifier.
        """
        parts = cron_expression.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression '{cron_expression}'. "
                "Expected format: 'minute hour day month day_of_week'"
            )

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        actual_job_id = job_id or self._job_id

        # Remove existing job if present
        if self._scheduler.get_job(actual_job_id):
            self._scheduler.remove_job(actual_job_id)

        self._scheduler.add_job(
            scan_fn,
            trigger=trigger,
            id=actual_job_id,
            name="Terraform Drift Scan",
            replace_existing=True,
        )

        logger.info(f"Scheduled drift scan: {cron_expression}")

    def start(self) -> None:
        """Start the scheduler."""
        if not self._is_running:
            self._scheduler.start()
            self._is_running = True
            logger.info("Drift scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        if self._is_running:
            self._scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("Drift scheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is currently running."""
        return self._is_running

    def get_next_run_time(self) -> str | None:
        """Get the next scheduled run time."""
        job = self._scheduler.get_job(self._job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None
