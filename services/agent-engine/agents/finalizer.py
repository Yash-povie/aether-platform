import asyncio
import concurrent.futures
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

import asyncpg
import aio_pika

from ..schemas import PipelineState

logger = logging.getLogger(__name__)

DATABASE_URL  = os.getenv("DATABASE_URL",  "postgresql://aether:aether@postgres:5432/aether")
RABBITMQ_URL  = os.getenv("RABBITMQ_URL",  "amqp://guest:guest@rabbitmq:5672/")
AUDIT_EXCHANGE = os.getenv("AUDIT_EXCHANGE", "audit")


# ---------------------------------------------------------------------------
# Async persistence helpers
# ---------------------------------------------------------------------------

async def _persist_report(
    job_id: str,
    org_id: str,
    report: dict,
    audit_trail: list,
) -> str:
    """Insert the report row, mark the job complete, and publish an audit event.

    Returns the newly created ``report_id`` on success.
    Raises on unrecoverable errors so the caller can log them.
    """
    # asyncpg needs a plain postgresql:// DSN (no +asyncpg dialect prefix)
    dsn = DATABASE_URL.replace("+asyncpg", "").replace("postgresql+psycopg", "postgresql")

    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    try:
        report_id = str(uuid.uuid4())
        summary = str(report.get("executive_summary", "Analysis complete"))[:500]
        now = datetime.now(timezone.utc)

        # Upsert report — idempotent on re-runs
        await conn.execute(
            """
            INSERT INTO reports (id, job_id, org_id, content, summary, version, created_at)
            VALUES ($1, $2::uuid, $3::uuid, $4::jsonb, $5, 1, $6)
            ON CONFLICT (job_id) DO UPDATE
                SET content    = EXCLUDED.content,
                    summary    = EXCLUDED.summary,
                    version    = reports.version + 1
            """,
            report_id,
            job_id,
            org_id,
            json.dumps(report, default=str),
            summary,
            now,
        )

        # Mark job as complete
        await conn.execute(
            "UPDATE jobs SET status = 'completed', completed_at = $1 WHERE id = $2::uuid",
            now,
            job_id,
        )

        logger.info("[Finalizer] Report %s persisted; job %s marked completed", report_id, job_id)

    finally:
        await conn.close()

    # Publish audit event to RabbitMQ (separate connection; non-fatal if broker is down)
    try:
        rmq_conn = await aio_pika.connect_robust(RABBITMQ_URL, timeout=10)
        async with rmq_conn:
            channel = await rmq_conn.channel()
            exchange = await channel.declare_exchange(
                AUDIT_EXCHANGE,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )

            # Include only the last 5 audit trail entries to keep payload small
            audit_snippet = audit_trail[-5:] if len(audit_trail) > 5 else audit_trail

            event_payload = {
                "event_id": str(uuid.uuid4()),
                "org_id": org_id,
                "event_type": "job.completed",
                "entity_type": "job",
                "entity_id": job_id,
                "payload": {
                    "report_id": report_id,
                    "audit_trail": audit_snippet,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(event_payload, default=str).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="audit.events",
            )

        logger.info("[Finalizer] Audit event published for job %s", job_id)

    except Exception as exc:
        # Broker unavailability must not roll back the DB write
        logger.error("[Finalizer] Failed to publish audit event for job %s: %s", job_id, exc)

    return report_id


def _run_async(coro) -> Any:
    """Run an async coroutine from a synchronous LangGraph node.

    If an event loop is already running (e.g. inside uvicorn / FastAPI),
    we spin up a new thread with its own loop to avoid 'Event loop is closed'
    and 'This event loop is already running' errors.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We are inside an already-running loop — offload to a fresh thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=45)
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def finalizer_node(state: PipelineState) -> Dict[str, Any]:
    """Persist the final report to PostgreSQL and publish an audit event.

    Bridges the sync LangGraph node contract with async I/O using a thread
    executor when needed.  All persistence errors are caught and logged so
    that the graph can still mark itself as complete rather than crashing.
    """
    job_id     = state.get("job_id", "")
    org_id     = state.get("org_id", "")
    # report_writer stores output under "final_report"
    report     = state.get("final_report") or {}
    audit_trail = list(state.get("audit_trail", []))

    logger.info("[Finalizer] Persisting report for job %s (org %s)", job_id, org_id)

    try:
        report_id = _run_async(
            _persist_report(job_id, org_id, report, audit_trail)
        )
        status = "complete"
    except Exception as exc:
        logger.error(
            "[Finalizer] Persistence failed for job %s: %s",
            job_id,
            exc,
            exc_info=True,
        )
        report_id = None
        status = "complete_with_errors"

    audit_trail.append({
        "step": "finalizer",
        "status": status,
        "report_id": report_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "audit_trail": audit_trail,
    }
