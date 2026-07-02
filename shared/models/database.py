from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Float, DateTime, Integer, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

class Organization(Base):
    __tablename__ = 'organizations'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    settings = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    __tablename__ = 'users'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'))
    email = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Job(Base):
    __tablename__ = 'jobs'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String, nullable=False, default='queued')
    input_files = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

class JobArtifact(Base):
    __tablename__ = 'job_artifacts'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey('jobs.id'))
    modality = Column(String, nullable=False)
    worker = Column(String, nullable=False)
    content_uri = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class HitlItem(Base):
    __tablename__ = 'hitl_items'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), nullable=False)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    finding = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=False)
    status = Column(String, nullable=False, default='pending')
    assigned_to = Column(UUID(as_uuid=True))
    decision_at = Column(DateTime(timezone=True))
    decision_by = Column(UUID(as_uuid=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Report(Base):
    __tablename__ = 'reports'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey('jobs.id'), unique=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    content = Column(JSONB, nullable=False)
    summary = Column(String)
    version = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True))
    event_type = Column(String, nullable=False)
    entity_type = Column(String)
    entity_id = Column(UUID(as_uuid=True))
    payload = Column(JSONB)
    ip_address = Column(INET)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
