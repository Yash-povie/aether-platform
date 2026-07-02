import os
import json
import logging
from typing import Dict, Any

from langchain_groq import ChatGroq
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ..schemas import PipelineState

logger = logging.getLogger(__name__)

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

_llm = None


def _get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        _llm = ChatGroq(model=GROQ_MODEL, temperature=0.1, max_retries=2)
    return _llm


_SYSTEM_PROMPT = """You are an expert intelligence analyst specialising in cross-modal evidence reconciliation.

You receive a JSON array of findings from different data modalities
(e.g. audio transcription, image OCR, video analysis, document text).

Your task:
1. Identify findings that corroborate each other across modalities (same underlying fact).
2. Identify findings that contradict each other.
3. Merge corroborating findings into a single reconciled finding listing every contributing modality.
4. Preserve unique single-modality findings that are still significant.
5. Assign a reconciled_confidence between 0.0 and 1.0:
   - Single-modality, high original confidence -> 0.55-0.75
   - Multi-modality agreement (2+ modalities) -> 0.80-0.95
   - Contradicted finding -> 0.30-0.50
6. Assign severity: "high" | "medium" | "low" based on the nature of the finding.

Return ONLY a valid JSON array (no markdown, no prose) where each element has:
{
  "finding_id": "<string>",
  "description": "<clear reconciled description>",
  "modalities": ["<modality_1>"],
  "support_count": <integer>,
  "contradictions": ["<any contradicting finding description>"],
  "severity": "high|medium|low",
  "reconciled_confidence": <float 0.0-1.0>,
  "original_ids": ["<id>"]
}"""

_USER_PROMPT = """Findings from {modality_count} distinct modalities:

{findings_json}

Reconcile these findings now. Return the JSON array only."""


def _deduplicate_fallback(findings):
    """Best-effort deduplication by description prefix when the LLM is unavailable."""
    seen = set()
    reconciled = []
    for f in findings:
        key = f.get("description", "")[:60].lower().strip()
        if key not in seen:
            seen.add(key)
            reconciled.append({
                **f,
                "modalities": [f.get("modality", "unknown")],
                "support_count": 1,
                "contradictions": [],
                "severity": "medium",
                "reconciled_confidence": f.get("confidence", 0.65),
                "original_ids": [f.get("id", "")],
            })
    return reconciled


def evidence_reconciler_node(state: PipelineState) -> Dict[str, Any]:
    """Cross-reference findings across modalities, deduplicate, and assign reconciled weight.

    Uses a Groq LLM for semantic reconciliation. Falls back to simple
    description-prefix deduplication if the LLM call fails.
    """
    findings = state.get("findings", [])
    logger.info("[EvidenceReconciler] Reconciling %d findings", len(findings))

    if not findings:
        return {
            "reconciled_findings": [],
            "audit_trail": state.get("audit_trail", []) + [
                {"step": "evidence_reconciler", "findings_in": 0, "reconciled": 0}
            ],
        }

    # Group by modality for the prompt header
    by_modality = {}
    for f in findings:
        mod = f.get("modality", "unknown")
        by_modality.setdefault(mod, []).append(f)

    # Truncate to avoid exceeding context window (~8k token budget)
    findings_sample = findings[:30]

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("user", _USER_PROMPT),
    ])

    try:
        chain = prompt | _get_llm() | JsonOutputParser()
        result = chain.invoke({
            "modality_count": len(by_modality),
            "findings_json": json.dumps(findings_sample, indent=2, default=str),
        })

        if isinstance(result, list):
            reconciled = result
        elif isinstance(result, dict):
            # Model sometimes wraps the array in a key
            reconciled = None
            for val in result.values():
                if isinstance(val, list):
                    reconciled = val
                    break
            if reconciled is None:
                reconciled = [result]
        else:
            raise ValueError(f"Unexpected LLM output type: {type(result)}")

    except Exception as exc:
        logger.warning(
            "[EvidenceReconciler] LLM reconciliation failed (%s) — using deduplication fallback",
            exc,
        )
        reconciled = _deduplicate_fallback(findings)

    logger.info(
        "[EvidenceReconciler] %d findings -> %d reconciled findings",
        len(findings),
        len(reconciled),
    )

    audit_entry = {
        "step": "evidence_reconciler",
        "findings_in": len(findings),
        "modalities": list(by_modality.keys()),
        "reconciled": len(reconciled),
    }

    return {
        "reconciled_findings": reconciled,
        "audit_trail": state.get("audit_trail", []) + [audit_entry],
    }
