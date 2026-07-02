import os
from minio import Minio
from fastapi import UploadFile, HTTPException
import uuid

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

RAW_BUCKET = "raw-uploads"

def ensure_bucket():
    if not client.bucket_exists(RAW_BUCKET):
        client.make_bucket(RAW_BUCKET)

async def stream_to_minio(file: UploadFile, org_id: str, job_id: str) -> str:
    ensure_bucket()
    file_ext = os.path.splitext(file.filename)[1]
    object_name = f"{org_id}/{job_id}/{uuid.uuid4()}{file_ext}"
    
    # Fastapi UploadFile has a file property that acts as a spooled temp file
    # We can pass it directly to Minio put_object to stream chunks
    size = file.size if file.size else -1
    
    try:
        client.put_object(
            RAW_BUCKET,
            object_name,
            file.file,
            length=size,
            part_size=10*1024*1024, # 10MB parts
            content_type=file.content_type
        )
        return f"minio://{RAW_BUCKET}/{object_name}"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO upload failed: {str(e)}")
