from typing import TypedDict, List, Dict, Optional, Any
from shared.models.schemas import Artifact, Finding, HitlItem, Report, AuditEvent

class PipelineState(TypedDict):
    job_id: str
    org_id: str
    task_description: Optional[str]
    artifacts: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    reconciled_findings: List[Dict[str, Any]]
    confidence_scores: Dict[str, float]
    hitl_items: List[Dict[str, Any]]
    hitl_decisions: Dict[str, str]
    final_report: Optional[Dict[str, Any]]
    audit_trail: List[Dict[str, Any]]