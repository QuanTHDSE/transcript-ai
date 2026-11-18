import os

from celery import Celery

celery_app = Celery("transcripto", broker=os.getenv("REDIS_URL", "redis://redis:6379/0"), include=["app.tasks", "app.assessments", "app.mapping"])
limit = max(60, int(os.getenv("JOB_TIME_LIMIT", "1800")))
celery_app.conf.update(
    worker_concurrency=max(1, int(os.getenv("WORKER_CONCURRENCY", "1"))),
    worker_prefetch_multiplier=1, task_acks_late=True, task_reject_on_worker_lost=True,
    task_soft_time_limit=limit - 10, task_time_limit=limit,
    broker_transport_options={"visibility_timeout": limit + 120},
    broker_connection_retry_on_startup=True,
    beat_schedule={"recover-queue": {"task": "transcripto.recover", "schedule": 30.0}},
)
