"""RQ worker entrypoint.

Runs the same ``keel`` codebase as the API (different entrypoint) — this is why
api and worker share one package.

Two things start here:
  1. A lightweight scheduler thread that periodically enqueues the recurring
     background jobs (outbox publisher, capacity sync, action-expiry sweep).
     ``rq`` has no built-in cron, and ``worker.work(with_scheduler=True)`` only
     runs jobs that were *already* scheduled — so without this loop the recurring
     jobs would never fire and the outbox/notify tier would stay idle.
  2. The RQ worker loop itself, which executes the enqueued jobs.

Run via the container CMD: ``rq worker -u <redis_url> keel`` would skip the
scheduler thread, so the container runs ``python -m keel.workers.main`` instead.
"""

from __future__ import annotations

import threading
import time

from redis import Redis
from rq import Queue, Worker

from keel.config import get_settings
from keel.logging import configure_logging, get_logger

QUEUE_NAME = "keel"

# Cadence (seconds) for each recurring job.
_OUTBOX_EVERY = 15
_CAPACITY_EVERY = 30
_EXPIRY_EVERY = 300


def _scheduler_loop(redis_url: str) -> None:
    """Daemon loop: periodically enqueue the recurring background jobs.

    Each tick enqueues at most one of each job. Failures are logged and never
    stop the loop — the next tick retries.
    """
    log = get_logger(__name__)
    connection = Redis.from_url(redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)

    from keel.workers.phase3_jobs import (
        capacity_sync_job,
        expiry_sweep_job,
        outbox_publisher_job,
    )

    last_capacity = 0.0
    last_expiry = 0.0
    log.info("worker_scheduler_starting")
    while True:
        now = time.monotonic()
        try:
            queue.enqueue(outbox_publisher_job)
            if now - last_capacity >= _CAPACITY_EVERY:
                queue.enqueue(capacity_sync_job)
                last_capacity = now
            if now - last_expiry >= _EXPIRY_EVERY:
                queue.enqueue(expiry_sweep_job)
                last_expiry = now
        except Exception as exc:  # noqa: BLE001 — scheduler must survive transient errors
            log.warning("worker_scheduler_enqueue_failed", error=str(exc))
        time.sleep(_OUTBOX_EVERY)


def main() -> None:
    settings = get_settings()
    configure_logging(service="keel-worker", level=settings.keel_log_level)
    # Same tracing path as the API: the worker does DB/Redis/email I/O, so
    # outbox publish + notification delivery show up in the same Jaeger UI.
    from keel.infra import tracing

    tracing.configure_tracing(
        service_name="keel-worker",
        otlp_endpoint=settings.otel_exporter_otlp_endpoint or None,
    )
    tracing.instrument_libraries()
    log = get_logger(__name__)

    # Start the recurring-job scheduler in a daemon thread.
    scheduler = threading.Thread(
        target=_scheduler_loop, args=(settings.redis_url,), name="keel-scheduler", daemon=True
    )
    scheduler.start()

    # RQ uses a synchronous Redis connection for the worker loop.
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)
    log.info("worker_starting", queue=QUEUE_NAME, redis=settings.redis_url)

    worker = Worker([queue], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
