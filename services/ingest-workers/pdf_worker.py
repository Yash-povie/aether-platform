import asyncio
import os
import json
import aio_pika
import fitz # PyMuPDF
import io
from minio import Minio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from shared.models.database import JobArtifact
import uuid

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://aether:aether@localhost:5432/aether")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)
PROCESSED_BUCKET = "processed"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

def ensure_bucket():
    if not minio_client.bucket_exists(PROCESSED_BUCKET):
        minio_client.make_bucket(PROCESSED_BUCKET)

async def process_pdf(message: aio_pika.IncomingMessage):
    async with message.process():
        try:
            payload = json.loads(message.body.decode())
            job_id = payload["job_id"]
            file_ref = payload["file_ref"] # minio://raw-uploads/...
            
            # 1. Fetch from Minio
            print(f"PDF Worker processing {file_ref} for job {job_id}")
            bucket, obj_name = file_ref.replace("minio://", "").split("/", 1)
            response = minio_client.get_object(bucket, obj_name)
            pdf_bytes = response.read()
            response.close()
            response.release_conn()
            
            # 2. PyMuPDF OCR/Extraction
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            extracted_text = ""
            for page in doc:
                extracted_text += page.get_text()
            
            # 3. Write heavy structured output back to MinIO processed/ bucket
            ensure_bucket()
            result_obj_name = f"{job_id}/pdf_artifact.json"
            data = io.BytesIO(json.dumps({"text": extracted_text.strip(), "pages": len(doc)}).encode())
            minio_client.put_object(PROCESSED_BUCKET, result_obj_name, data, length=data.getbuffer().nbytes)
            
            content_uri = f"minio://{PROCESSED_BUCKET}/{result_obj_name}"
            
            # 4. Write metadata and content_uri pointer to Postgres
            artifact = JobArtifact(
                job_id=uuid.UUID(job_id),
                modality="pdf",
                worker="pdf_worker",
                content_uri=content_uri
            )
            
            async with AsyncSessionLocal() as session:
                session.add(artifact)
                await session.commit()
                
            # 5. Publish completion event to agent.ready queue
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            channel = await connection.channel()
            await channel.default_exchange.publish(
                aio_pika.Message(body=json.dumps({"job_id": job_id, "status": "ready"}).encode()),
                routing_key="agent.ready"
            )
            await connection.close()
            
            print(f"PDF Worker successfully processed job {job_id}")
        except Exception as e:
            print(f"PDF Worker failed: {e}")

async def main():
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue("ingest.pdf", durable=True)
        print("PDF Worker connected to RabbitMQ. Waiting for messages...")
        await queue.consume(process_pdf)
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
