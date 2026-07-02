"""RabbitMQ consumer that drives the LangGraph agent pipeline.

Two queues are consumed concurrently:

``agent.ready``
    Published by the job-submission API when all worker artifacts are ready.
    Message body: ``{"job_id": "<uuid>", "org_id": "<uuid>"}``
    Action: fetch artifacts from PostgreSQL, build initial state, run the graph.

``hitl.decisions``
    Published by the reviewer UI / API when a human approves or rejects findings.
    Message body:
        ``{"job_id": "<uuid>", "decision": "approve|reject", "reviewer_id": "<uuid>"}``
    Action: call ``resume_graph()`` to continue a suspended pipeline.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any, Dict, List

import aio_pika
import asyncpg

from .graph import agent_engine, resume_graph

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RABBITMQ_URL   = os.getenv("RABBITMQ_URL",        "amqp://guest:guest@rabbitmq:5672/")
DATABASE_URL   = os.getenv("DATABASE_URL",         "postgresql://aether:aether@postgres:5432/aether")

QUEUE_JOBS     = os.getenv("QUEUE_AGENT_READY",    "agent.ready")
QUEUE_HITL     = os.getenv("QUEUE_HITL_DECISIONS", "hitl.decisions")
PREFETCH_COUNT = int(os.getenv("WORKER_PREFETCH",  "4"))


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _pg_dsn() -> str:
    return (
        DATABASE_URL
        .replace("+asyncpg", "")
        .replace("+psycopg2", "")
        .replace("postgresql+psycopg", "postgresql")
    )


async def fetch_job_artifacts(job_id: str) -> List[Dict[str, Any]]:
    """Return all artifact rows for *job_id* from ``job_artifacts``."""
    conn = await asyncpg.connect(_pg_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT id::text, job_id::text, modality, worker, content_uri,
                   created_at::text
            FROM   job_artifacts
            WHERE  job_id = $1::uuid
            ORDER  BY created_at
            """,
            job_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def fetch_job_org(job_id: str) -> str:
    """Return the ``org_id`` for *job_id*, or empty string if not found."""
    conn = await asyncpg.connect(_pg_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT org_id::text FROM jobs WHERE id = $1::uuid", job_id
        )
        return row["org_id"] if row else ""
    finally:
        await conn.close()


async def mark_job_processing(job_id: str) -> None:
    conn = await asyncpg.connect(_pg_dsn())
    try:
        await conn.execute(
            "UPDATE jobs SET status = 'processing' WHERE id = $1::uuid AND status = 'queued'",
            job_id,
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def handle_agent_ready(message: aio_pika.IncomingMessage) -> None:
    """Process an ``agent.ready`` message — run the full pipeline for the job."""
    async with message.process(requeue=False):
        try:
            body = json.loads(message.body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("[Worker] Malformed agent.ready message: %s", exc)
            return

        job_id = body.get("job_id", "")
        org_id = body.get("org_id", "")

        if not job_id:
            logger.error("[Worker] agent.ready message missing job_id — discarding")
            return

        logger.info("[Worker] Received agent.ready for job %s (org %s)", job_id, org_id)

        if not org_id:
            try:
                org_id = await fetch_job_org(job_id)
            except Exception as exc:
                logger.error("[Worker] Could not fetch org for job %s: %s", job_id, exc)
                org_id = ""

        try:
            artifacts = await fetch_job_artifacts(job_id)
        except Exception as exc:
            logger.error("[Worker] Failed to fetch artifacts for job %s: %s", job_id, exc)
            return

        if not artifacts:
            logger.warning("[Worker] No artifacts found for job %s — skipping", job_id)
            return

        logger.info("[Worker] Job %s — %d artifact(s) found, starting pipeline", job_id, len(artifacts))

        await mark_job_processing(job_id)

        initial_state: Dict[str, Any] = {
            "job_id": job_id,
            "org_id": org_id,
            "task_description": None,
            "artifacts": artifacts,
            "findings": [],
            "reconciled_findings": [],
            "confidence_scores": {},
            "hitl_items": [],
            "hitl_decisions": {},
            "final_report": None,
            "audit_trail": [],
        }

        config = {"configurable": {"thread_id": job_id}}

        try:
            final_state = await agent_engine.ainvoke(initial_state, config=config)
            last_step = (final_state.get("audit_trail") or [{}])[-1]
            logger.info(
                "[Worker] Pipeline complete for job %s — step=%s status=%s",
                job_id,
                last_step.get("step", "?"),
                last_step.get("status", "suspended_for_hitl"),
            )
        except Exception as exc:
            logger.error("[Worker] Pipeline failed for job %s: %s", job_id, exc, exc_info=True)


async def handle_hitl_decision(message: aio_pika.IncomingMessage) -> None:
    """Process a ``hitl.decisions`` message — resume a suspended pipeline."""
    async with message.process(requeue=False):
        try:
            body = json.loads(message.body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("[Worker] Malformed hitl.decisions message: %s", exc)
            return

        job_id      = body.get("job_id", "")
        decision    = body.get("decision", "")
        reviewer_id = body.get("reviewer_id", "system")

        if not job_id or not decision:
            logger.error("[Worker] hitl.decisions message missing job_id or decision — discarding")
            return

        logger.info(
            "[Worker] HITL decision for job %s — decision=%s reviewer=%s",
            job_id, decision, reviewer_id,
        )

        try:
            await resume_graph(job_id=job_id, decision=decision, reviewer_id=reviewer_id)
            logger.info("[Worker] Graph resumed and completed for job %s", job_id)
        except ValueError as exc:
            logger.warning("[Worker] Cannot resume job %s: %s", job_id, exc)
        except Exception as exc:
            logger.error("[Worker] Resume failed for job %s: %s", job_id, exc, exc_info=True)


# ---------------------------------------------------------------------------
# Main consumer loop
# ---------------------------------------------------------------------------

async def _consume(stop_event: asyncio.Event) -> None:
    connection = await aio_pika.connect_robust(
        RABBITMQ_URL,
        reconnect_interval=5,
        fail_fast=False,
    )

    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=PREFETCH_COUNT)

        jobs_queue = await channel.declare_queue(QUEUE_JOBS, durable=True)
        hitl_queue = await channel.declare_queue(QUEUE_HITL, durable=True)

        await jobs_queue.consume(handle_agent_ready)
        await hitl_queue.consume(handle_hitl_decision)

        logger.info(
            "[Worker] Consuming from '%s' and '%s' (prefetch=%d)",
            QUEUE_JOBS, QUEUE_HITL, PREFETCH_COUNT,
        )

        await stop_event.wait()
        logger.info("[Worker] Shutdown signal received — draining and closing")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals
            signal.signal(sig, lambda *_: stop_event.set())

    await _consume(stop_event)


if __name__ == "__main__":
    asyncio.run(main())
