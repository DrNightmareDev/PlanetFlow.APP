"""Celery application - broker: RabbitMQ, results: rpc (fire-and-forget)."""
import os

from celery import Celery

celery_app = Celery(
    "planetflow",
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
    task_routes={
        "app.tasks.zkill_websocket_subscriber": {"queue": "ws"},
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "poll-r2z2-intel-live": {
            "task": "app.tasks.zkill_websocket_subscriber",
            "schedule": 3.0,
            "options": {
                "queue": "ws",
                "expires": 2,
            },
        },
        "auto-refresh-stale-accounts": {
            "task": "app.tasks.auto_refresh_stale_accounts",
            "schedule": 300.0,
        },
        "refresh-market-prices": {
            "task": "app.tasks.refresh_market_prices_task",
            "schedule": 900.0,
        },
        "cleanup-sso-states": {
            "task": "app.tasks.cleanup_sso_states_task",
            "schedule": 3600.0,
        },
        "send-webhook-alerts": {
            "task": "app.tasks.send_webhook_alerts_task",
            "schedule": 900.0,
        },
    },
)
