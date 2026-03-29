"""Celery application — broker: RabbitMQ, results: rpc (fire-and-forget)."""
import os

from celery import Celery

celery_app = Celery(
    "eve_pi_manager",
    broker=os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//"),
    backend="rpc://",
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,       # fair dispatch — one task at a time per worker
    task_acks_late=True,                # ack only after task completes (safe retry on crash)
    task_reject_on_worker_lost=True,    # requeue if worker dies mid-task
    beat_schedule={
        # Auto-refresh stale colony caches every 5 minutes
        "auto-refresh-stale-accounts": {
            "task": "app.tasks.auto_refresh_stale_accounts",
            "schedule": 300.0,
        },
        # Market price refresh every 15 minutes
        "refresh-market-prices": {
            "task": "app.tasks.refresh_market_prices_task",
            "schedule": 900.0,
        },
        # SSO state cleanup every hour
        "cleanup-sso-states": {
            "task": "app.tasks.cleanup_sso_states_task",
            "schedule": 3600.0,
        },
        # Discord/webhook expiry alerts every 15 minutes
        "send-webhook-alerts": {
            "task": "app.tasks.send_webhook_alerts_task",
            "schedule": 900.0,
        },
    },
)
