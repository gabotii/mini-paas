"""Celery application shared by the worker, the scheduler (beat) and the API (producer only)."""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery("mini_paas", broker=settings.broker_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=False,  # a half-run pipeline is failed by the reaper, not re-run
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": settings.build_timeout_seconds * 2 + 600},
    task_default_queue="ops",
    task_routes={
        "deployments.run": {"queue": "deployments"},
        "maintenance.*": {"queue": "maintenance"},
        "projects.*": {"queue": "ops"},
    },
    beat_schedule={
        "reap-stale-deployments": {"task": "maintenance.reap_stale", "schedule": 60.0},
        "cleanup": {"task": "maintenance.cleanup", "schedule": 3600.0},
        "worker-heartbeat": {"task": "maintenance.heartbeat", "schedule": 30.0},
    },
    timezone="UTC",
)


@setup_logging.connect
def _setup_logging(**_: object) -> None:
    configure_logging(settings.log_level, settings.log_json)


@worker_process_init.connect
def _reset_connections(**_: object) -> None:
    """Forked worker processes must not reuse the parent's DB/Redis/Docker connections."""
    from app.core.redis import get_sync_redis
    from app.db.session import get_sync_engine

    get_sync_engine().dispose(close=False)
    get_sync_redis.cache_clear()
