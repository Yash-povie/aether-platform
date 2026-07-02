import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from typing import List
from shared.auth.rbac import get_current_user
from ..utils.minio_client import stream_to_minio
from ..utils.rabbitmq_client import rabbitmq

router = APIRouter(prefix="/api/v1/ingest", tags=["Ingestion"])

def get_modality_from_extension(filename: str) -> str:
    ext = filename.split(".")[-1].lower()
    if ext == "pdf":
        return "pdf"
    elif ext in ["jpg", "jpeg", "png", "webp"]:
        return "image"
    elif ext in ["mp4", "avi", "mov"]:
        return "video"
    elif ext == "csv":
        return "csv"
    return "unknown"

@router.post("")
async def ingest_files(
    files: List[UploadFile] = File(...),
    user: dict = Depends(get_current_user)
):
    org_id = user["org_id"]
    job_id = str(uuid.uuid4())
    
    # 1. We would typically create a Job record in Postgres here.
    # We will simulate this or it can be wired up via SQLAlchemy.

    # 2. Upload files and queue tasks
    dispatched_workers = []
    
    for file in files:
        modality = get_modality_from_extension(file.filename)
        if modality == "unknown":
            continue
            
        # Stream directly to MinIO (prevents OOM on large videos)
        file_uri = await stream_to_minio(file, org_id, job_id)
        
        # Determine target queue based on modality
        queue_name = f"ingest.{modality}"
        
        payload = {
            "job_id": job_id,
            "org_id": org_id,
            "file_ref": file_uri,
            "modality": modality,
            "metadata": {"filename": file.filename},
            "callback_queue": "agent.ready"
        }
        
        await rabbitmq.publish_message(queue_name, payload)
        dispatched_workers.append({"modality": modality, "file": file.filename, "uri": file_uri})
        
    # 3. Publish an audit event
    audit_payload = {
        "event_type": "ingest.started",
        "org_id": org_id,
        "user_id": user["user_id"],
        "payload": {"job_id": job_id, "files": dispatched_workers}
    }
    await rabbitmq.publish_event("audit.events", "audit.ingest", audit_payload)

    return {"job_id": job_id, "status": "processing", "dispatched": dispatched_workers}
