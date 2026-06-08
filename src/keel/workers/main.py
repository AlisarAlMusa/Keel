"""RQ worker entrypoint.

Runs the same ``keel`` codebase as the API (different entrypoint) — this is why
api and worker share one package. Background jobs (capacity sync, waitlist,
outbox publisher, alerts, auto-replan) are registered in later phases.

Run via the container CMD: ``rq worker -u <redis_url> keel``. This module also
supports ``python -m keel.workers.main`` for local use.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from keel.config import get_settings
from keel.logging import configure_logging, get_logger

QUEUE_NAME = "keel"


def main() -> None:
    settings = get_settings()
    configure_logging(service="keel-worker", level=settings.keel_log_level)
    log = get_logger(__name__)

    # RQ uses a synchronous Redis connection for the worker loop.
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)
    log.info("worker_starting", queue=QUEUE_NAME, redis=settings.redis_url)

    worker = Worker([queue], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
