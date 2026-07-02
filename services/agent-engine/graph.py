import logging
import os
from typing import Any, Dict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from .schemas import PipelineState
from .agents.coordinator import coordinator_node
from .agents.anomaly_detector import anomaly_detector_node
from .agents.evidence_reconciler import evidence_reconciler_node
from .agents.confidence_scorer import confidence_scorer_node
from .agents.report_writer import report_writer_node
from .agents.finalizer import finalizer_node
from .agents.pii_redactor import pii_redactor_node

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aether:aether@postgres:5432/aether")

# ---------------------------------------------------------------------------
# HITL conditional edge
# ---------------------------------------------------------------------------

def hitl_gate(state: PipelineState) -> str:
    """Route to HITL suspension or straight to the finalizer.

    A finding requires human review when its confidence score is below the
    threshold AND no reviewer decision has already been recorded for it.
    This means the gate is re-evaluated safely on graph resume: once
    ``hitl_decisions`` contains an entry for every low-confidence finding,
    the gate routes to ``finalizer``.
    """
    threshold = float(os.getenv("HITL_CONFIDENCE_THRESHOLD", "0.75"))
    confidence_scores: Dict[str, float] = state.get("confidence_scores", {})
    hitl_decisions: Dict[str, str] = state.get("hitl_decisions", {})

    pending_review = [
        f
        for f in state.get("reconciled_findings", [])
        if confidence_scores.get(f.get("id", f.get("finding_id", "")), 1.0) < threshold
        and f.get("id", f.get("finding_id", "")) not in hitl_decisions
    ]

    if pending_review:
        logger.info(
            "[HITLGate] %d finding(s) require human review (threshold=%.2f)",
            len(pending_review),
            threshold,
        )
        return "hitl_queue"

    return "finalizer"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

workflow = StateGraph(PipelineState)

workflow.add_node("pii_redactor",       pii_redactor_node)
workflow.add_node("coordinator",        coordinator_node)
workflow.add_node("anomaly_detector",   anomaly_detector_node)
workflow.add_node("evidence_reconciler", evidence_reconciler_node)
workflow.add_node("confidence_scorer",  confidence_scorer_node)
workflow.add_node("report_writer",      report_writer_node)
workflow.add_node("finalizer",          finalizer_node)

workflow.set_entry_point("pii_redactor")
workflow.add_edge("pii_redactor",        "coordinator")
workflow.add_edge("coordinator",         "anomaly_detector")
workflow.add_edge("anomaly_detector",    "evidence_reconciler")
workflow.add_edge("evidence_reconciler", "confidence_scorer")
workflow.add_edge("confidence_scorer",   "report_writer")

workflow.add_conditional_edges(
    "report_writer",
    hitl_gate,
    {
        "hitl_queue": END,   # Graph suspends here; checkpoint is preserved
        "finalizer":  "finalizer",
    },
)

workflow.add_edge("finalizer", END)

# ---------------------------------------------------------------------------
# Checkpointer — Postgres-backed for crash resilience
# ---------------------------------------------------------------------------

# psycopg pool uses a plain postgresql:// DSN (no +asyncpg / +psycopg2 suffix)
_pool_dsn = (
    DATABASE_URL
    .replace("+asyncpg", "")
    .replace("+psycopg2", "")
    .replace("postgresql+psycopg", "postgresql")
)
pool = ConnectionPool(conninfo=_pool_dsn)
checkpointer = PostgresSaver(pool)
checkpointer.setup()

agent_engine = workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# HITL resume helper
# ---------------------------------------------------------------------------

async def resume_graph(
    job_id: str,
    decision: str,
    reviewer_id: str,
) -> Dict[str, Any]:
    """Resume a graph execution that suspended at the HITL gate.

    Steps:
    1. Load the checkpointed state for this job (thread_id = job_id).
    2. Merge the reviewer's decision into ``hitl_decisions``.
    3. Use ``update_state(..., as_node="report_writer")`` so LangGraph
       re-evaluates the conditional edge from ``report_writer``.
    4. Invoke the graph with ``None`` input to continue from the checkpoint.
    5. Return the completed state.

    Args:
        job_id:      The job UUID — used as the LangGraph thread_id.
        decision:    Reviewer decision string, e.g. ``"approve"`` or
                     ``"reject"``.  The worker/API layer is responsible for
                     mapping RabbitMQ message fields to these values.
        reviewer_id: UUID of the human reviewer for audit purposes.

    Returns:
        The final ``PipelineState`` dict after the graph completes.

    Raises:
        ValueError: If no checkpoint exists for ``job_id``.
        RuntimeError: If the graph fails to reach END after resume.
    """
    config = {"configurable": {"thread_id": job_id}}

    # 1. Load current checkpointed state
    current = agent_engine.get_state(config)
    if current is None or current.values is None:
        raise ValueError(f"No checkpoint found for job_id={job_id}")

    existing_decisions: Dict[str, str] = dict(
        current.values.get("hitl_decisions", {})
    )
    existing_audit: list = list(current.values.get("audit_trail", []))

    # 2. Find which findings are still pending and record the decision for all
    confidence_scores: Dict[str, float] = current.values.get("confidence_scores", {})
    threshold = float(os.getenv("HITL_CONFIDENCE_THRESHOLD", "0.75"))

    pending_ids = [
        f.get("id", f.get("finding_id", ""))
        for f in current.values.get("reconciled_findings", [])
        if confidence_scores.get(f.get("id", f.get("finding_id", "")), 1.0) < threshold
        and f.get("id", f.get("finding_id", "")) not in existing_decisions
    ]

    for fid in pending_ids:
        existing_decisions[fid] = decision

    existing_audit.append({
        "step": "hitl_resume",
        "reviewer_id": reviewer_id,
        "decision": decision,
        "findings_decided": pending_ids,
    })

    # 3. Inject updated state as if report_writer just produced it
    #    This causes LangGraph to re-run the conditional edge from report_writer.
    agent_engine.update_state(
        config,
        {"hitl_decisions": existing_decisions, "audit_trail": existing_audit},
        as_node="report_writer",
    )

    logger.info(
        "[ResumeGraph] Resuming job %s — reviewer=%s decision=%s pending_findings=%d",
        job_id,
        reviewer_id,
        decision,
        len(pending_ids),
    )

    # 4. Continue execution (input=None means "continue from checkpoint")
    final_state = await agent_engine.ainvoke(None, config=config)

    logger.info("[ResumeGraph] Job %s completed after HITL resume", job_id)
    return final_state
