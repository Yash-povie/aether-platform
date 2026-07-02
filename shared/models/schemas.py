from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID, uuid4

class AuditEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    org_id: UUID
    user_id: Optional[UUID] = None
    event_type: str
    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    payload: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Artifact(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    modality: str
    worker: str
    content_uri: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Job(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    org_id: UUID
    created_by: UUID
    status: str = "queued"
    input_files: Optional[List[Dict[str, str]]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

class Finding(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    type: str
    description: str
    confidence: float
    evidence: Optional[Dict[str, Any]] = None

class HitlItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    org_id: UUID
    finding: Finding
    confidence: float
    status: str = "pending"
    assigned_to: Optional[UUID] = None
    decision_at: Optional[datetime] = None
    decision_by: Optional[UUID] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Report(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    org_id: UUID
    content: Dict[str, Any]
    summary: Optional[str] = None
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
