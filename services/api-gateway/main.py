import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select, text

from config import settings
from routers import ingest
from shared.auth.rbac import get_current_user, require_role
from shared.models.database import AuditEvent, HitlItem, Job, Report, User
from shared.observability.telemetry import setup_telemetry
from utils.db import AsyncSessionLocal, engine, get_db
from utils.rabbitmq_client import rabbitmq

# ---------------------------------------------------------------------------
# Structured JSON Logging
# ---------------------------------------------------------------------------
try:
    from pythonjsonlogger import jsonlogger

    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "asctime": "timestamp"},
        )
    )
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [_handler]
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

logger = logging.getLogger("api-gateway")

# ---------------------------------------------------------------------------
# Rate Limiter (slowapi)
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("api-gateway starting up")
    try:
        await rabbitmq.connect()
        logger.info("RabbitMQ connected")
    except Exception as exc:
        logger.warning(
            "RabbitMQ connect failed at startup — will retry on demand",
            extra={"error": str(exc)},
        )
    yield
    # Teardown
    try:
        if rabbitmq.connection and not rabbitmq.connection.is_closed:
            await rabbitmq.connection.close()
    except Exception:
        pass
    await engine.dispose()
    logger.info("api-gateway shut down")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Aether API Gateway",
    version="1.0.0",
    description="Multimodal Intelligence Platform — API Gateway",
    lifespan=lifespan,
)

# OpenTelemetry (must be configured right after app creation)
setup_telemetry(app, service_name="api-gateway", engine=engine)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Correlation-ID middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# ---------------------------------------------------------------------------
# RabbitMQ dependency
# ---------------------------------------------------------------------------
async def get_rabbitmq():
    """Return a live RabbitMQ client, reconnecting if the channel dropped."""
    if not rabbitmq.channel or rabbitmq.channel.is_closed:
        await rabbitmq.connect()
    return rabbitmq


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(ingest.router)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


# ===========================================================================
# Auth Endpoints
# ===========================================================================

@app.post("/api/v1/auth/login", tags=["Auth"])
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db=Depends(get_db)):
    """Authenticate with email + password; return RS256 JWT."""
    from passlib.context import CryptContext
    from shared.auth.jwt_handler import create_access_token

    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    password_hash = getattr(user, "password_hash", None)
    if password_hash is None or not pwd_ctx.verify(body.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    token_payload = {
        "sub": str(user.id),
        "user_id": str(user.id),
        "org_id": str(user.org_id),
        "role": user.role,
    }
    access_token = create_access_token(token_payload)
    expire_seconds = int(os.getenv("JWT_EXPIRE_MINUTES", "60")) * 60
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": expire_seconds,
    }


@app.get("/api/v1/auth/jwks", tags=["Auth"])
async def get_jwks():
    """Expose the RS256 public key in JWKS format for token consumers."""
    from jose import jwk
    from shared.auth.jwt_handler import get_public_key

    try:
        public_key_pem = get_public_key()
        key = jwk.construct(public_key_pem, algorithm="RS256")
        key_dict = key.public_key().to_dict()
        key_dict["use"] = "sig"
        key_dict["alg"] = "RS256"
        key_dict["kid"] = "aether-gateway-v1"
        return {"keys": [key_dict]}
    except Exception as exc:
        logger.error("JWKS export failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Could not export public key")


# ===========================================================================
# Job Endpoints
# ===========================================================================

@app.get("/api/v1/jobs/{job_id}", tags=["Jobs"])
@limiter.limit("1000/minute")
async def get_job(
    request: Request,
    job_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return job status; enforces org-level isolation."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.org_id == user["org_id"])
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": str(job.id),
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "org_id": str(job.org_id),
        "input_files": job.input_files,
    }


@app.get("/api/v1/jobs/{job_id}/report", tags=["Jobs"])
@limiter.limit("1000/minute")
async def get_report(
    request: Request,
    job_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the completed analysis report for a job."""
    result = await db.execute(
        select(Report).where(
            Report.job_id == job_id, Report.org_id == user["org_id"]
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=404, detail="Report not found or job still processing"
        )
    return {
        "job_id": job_id,
        "report_id": str(report.id),
        "summary": report.summary,
        "content": report.content,
        "version": report.version,
        "created_at": report.created_at.isoformat(),
    }


# ===========================================================================
# HITL Endpoints
# ===========================================================================

@app.get("/api/v1/hitl/queue", tags=["HITL"])
@limiter.limit("1000/minute")
async def get_hitl_queue(
    request: Request,
    user: dict = Depends(require_role("analyst", "admin")),
    db=Depends(get_db),
):
    """Return pending HITL items for this org, ordered by ascending confidence."""
    result = await db.execute(
        select(HitlItem)
        .where(HitlItem.org_id == user["org_id"], HitlItem.status == "pending")
        .order_by(HitlItem.confidence.asc())
        .limit(50)
    )
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": str(i.id),
                "job_id": str(i.job_id),
                "finding": i.finding,
                "confidence": i.confidence,
                "created_at": i.created_at.isoformat(),
            }
            for i in items
        ]
    }


@app.post("/api/v1/hitl/{item_id}/approve", tags=["HITL"])
@limiter.limit("1000/minute")
async def approve_hitl(
    request: Request,
    item_id: str,
    user: dict = Depends(require_role("analyst", "admin")),
    db=Depends(get_db),
    rmq=Depends(get_rabbitmq),
):
    """Approve a HITL finding; publishes decision to hitl.decisions queue."""
    result = await db.execute(
        select(HitlItem).where(
            HitlItem.id == item_id, HitlItem.org_id == user["org_id"]
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="HITL item not found")
    if item.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Item already resolved with status: {item.status}",
        )

    item.status = "approved"
    item.decision_by = user["user_id"]
    item.decision_at = datetime.now(timezone.utc)
    await db.commit()

    await rmq.publish_message(
        "hitl.decisions",
        {
            "item_id": item_id,
            "job_id": str(item.job_id),
            "decision": "approve",
            "reviewer_id": user["user_id"],
        },
    )
    logger.info(
        "HITL approved",
        extra={
            "item_id": item_id,
            "reviewer": user["user_id"],
            "correlation_id": getattr(request.state, "correlation_id", ""),
        },
    )
    return {"status": "approved", "item_id": item_id}


@app.post("/api/v1/hitl/{item_id}/reject", tags=["HITL"])
@limiter.limit("1000/minute")
async def reject_hitl(
    request: Request,
    item_id: str,
    user: dict = Depends(require_role("analyst", "admin")),
    db=Depends(get_db),
    rmq=Depends(get_rabbitmq),
):
    """Reject a HITL finding; publishes decision to hitl.decisions queue."""
    result = await db.execute(
        select(HitlItem).where(
            HitlItem.id == item_id, HitlItem.org_id == user["org_id"]
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="HITL item not found")
    if item.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Item already resolved with status: {item.status}",
        )

    item.status = "rejected"
    item.decision_by = user["user_id"]
    item.decision_at = datetime.now(timezone.utc)
    await db.commit()

    await rmq.publish_message(
        "hitl.decisions",
        {
            "item_id": item_id,
            "job_id": str(item.job_id),
            "decision": "reject",
            "reviewer_id": user["user_id"],
        },
    )
    logger.info(
        "HITL rejected",
        extra={
            "item_id": item_id,
            "reviewer": user["user_id"],
            "correlation_id": getattr(request.state, "correlation_id", ""),
        },
    )
    return {"status": "rejected", "item_id": item_id}


# ===========================================================================
# Audit Endpoints
# ===========================================================================

@app.get("/api/v1/audit/events", tags=["Audit"])
@limiter.limit("1000/minute")
async def get_audit_events(
    request: Request,
    user: dict = Depends(require_role("admin")),
    db=Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """Return paginated audit events for this org (admin only)."""
    if limit > 500:
        raise HTTPException(status_code=400, detail="limit must be <= 500")

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.org_id == user["org_id"])
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    events = result.scalars().all()
    return {
        "events": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": str(e.entity_id) if e.entity_id else None,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "limit": limit,
        "offset": offset,
    }


# ===========================================================================
# Observability Endpoints
# ===========================================================================

@app.get("/metrics", tags=["Observability"], include_in_schema=False)
async def metrics():
    """Prometheus metrics scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health", tags=["Observability"])
async def health(db=Depends(get_db)):
    """Liveness + readiness probe — checks DB connectivity."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Database unavailable: {exc}"
        )

    rmq_status = (
        "ok"
        if (rabbitmq.connection and not rabbitmq.connection.is_closed)
        else "degraded"
    )
    return {
        "status": "healthy",
        "database": db_status,
        "rabbitmq": rmq_status,
        "service": "api-gateway",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)