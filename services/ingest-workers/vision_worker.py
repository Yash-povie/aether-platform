"""
vision_worker.py — Anthropic Claude Vision API image analysis worker.

Consumes from: ingest.image (RabbitMQ)
Publishes to:  agent (topic exchange) -> agent.ready
Stores:        MinIO processed/<job_id>/image_artifact.json
DB:            job_artifacts row (modality=image, worker=vision_worker)
"""

import asyncio
import os
import json
import logging
import base64
import io
import re
import uuid
import imghdr

import aio_pika
import anthropic
from dotenv import load_dotenv
from minio import Minio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Support import from Docker context (services/ingest-workers/) and local runs
try:
    from shared.models.database import JobArtifact
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
    from shared.models.database import JobArtifact

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RABBITMQ_URL   = os.getenv("RABBITMQ_URL",    "amqp://guest:guest@localhost:5672/")
DATABASE_URL   = os.getenv("DATABASE_URL",    "postgresql+asyncpg://aether:aether@localhost:5432/aether")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY")

PROCESSED_BUCKET = "processed"

MEDIA_TYPE_MAP = {
    "jpeg": "image/jpeg",
    "jpg":  "image/jpeg",
    "png":  "image/png",
    "gif":  "image/gif",
    "webp": "image/webp",
    "bmp":  "image/bmp",
    "tiff": "image/tiff",
}

# ---------------------------------------------------------------------------
# Clients (module-level; safe for a single-process worker)
# ---------------------------------------------------------------------------
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS,
    secret_key=MINIO_SECRET,
    secure=False,
)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_bucket() -> None:
    if not minio_client.bucket_exists(PROCESSED_BUCKET):
        minio_client.make_bucket(PROCESSED_BUCKET)


def _detect_media_type(image_data: bytes) -> str:
    img_type = imghdr.what(None, image_data) or "jpeg"
    return MEDIA_TYPE_MAP.get(img_type, "image/jpeg")


def _extract_json(text: str) -> dict:
    """Try to parse JSON from LLM response — handles markdown fences gracefully."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {
        "captions": text.strip(),
        "anomaly_flags": [],
        "objects_detected": [],
        "quality_score": 0.75,
        "confidence": 0.6,
    }

# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def process_image(job_id: str, file_ref: str) -> str:
    """
    Download image from MinIO, analyse with Claude Vision, write result back.
    Returns the content_uri of the stored JSON artifact.
    """
    logger.info("Downloading image for job %s from %s", job_id, file_ref)
    bucket, obj_name = file_ref.replace("minio://", "").split("/", 1)
    response = minio_client.get_object(bucket, obj_name)
    try:
        image_data = response.read()
    finally:
        response.close()
        response.release_conn()

    media_type = _detect_media_type(image_data)
    b64_image  = base64.standard_b64encode(image_data).decode("utf-8")

    logger.info("Calling Anthropic Vision for job %s (media_type=%s, bytes=%d)",
                job_id, media_type, len(image_data))

    message = anthropic_client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Analyze this image for anomalies, defects, and key findings.\n"
                            "Return a JSON object with:\n"
                            "{\n"
                            '  "captions": "brief description of what is in the image",\n'
                            '  "anomaly_flags": ["list of anomalies or issues detected"],\n'
                            '  "objects_detected": ["list of key objects/entities"],\n'
                            '  "quality_score": 0.0-1.0,\n'
                            '  "confidence": 0.0-1.0\n'
                            "}\n"
                            "Return only valid JSON, no markdown fences."
                        ),
                    },
                ],
            }
        ],
    )

    raw_text = message.content[0].text
    logger.debug("Anthropic raw response for job %s: %s", job_id, raw_text[:300])
    analysis = _extract_json(raw_text)

    # Persist result to MinIO
    ensure_bucket()
    result_key  = f"{job_id}/image_artifact.json"
    output_data = json.dumps(analysis, indent=2).encode("utf-8")
    minio_client.put_object(
        PROCESSED_BUCKET,
        result_key,
        io.BytesIO(output_data),
        length=len(output_data),
        content_type="application/json",
    )
    return f"minio://{PROCESSED_BUCKET}/{result_key}"


async def _write_artifact(job_id: str, content_uri: str) -> None:
    artifact = JobArtifact(
        job_id=uuid.UUID(job_id),
        modality="image",
        worker="vision_worker",
        content_uri=content_uri,
    )
    async with AsyncSessionLocal() as session:
        session.add(artifact)
        await session.commit()


async def _publish_ready(channel: aio_pika.Channel, job_id: str) -> None:
    exchange = await channel.declare_exchange(
        "agent", aio_pika.ExchangeType.TOPIC, durable=True
    )
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps({"job_id": job_id, "modality": "image"}).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key="agent.ready",
    )

# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def handle_message(message: aio_pika.IncomingMessage, channel: aio_pika.Channel) -> None:
    async with message.process(requeue_on_timeout=True):
        try:
            payload  = json.loads(message.body.decode())
            job_id   = payload["job_id"]
            file_ref = payload["file_ref"]
            logger.info("Vision worker received job %s", job_id)

            content_uri = await process_image(job_id, file_ref)
            await _write_artifact(job_id, content_uri)
            await _publish_ready(channel, job_id)

            logger.info("Vision worker completed job %s -> %s", job_id, content_uri)
        except KeyError as exc:
            logger.error("Malformed message (missing key %s): %s", exc, message.body[:200])
        except Exception:
            logger.exception("Vision worker failed for message: %s", message.body[:200])
            raise  # re-raise so aio_pika can nack / requeue

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Vision worker starting...")

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("ingest.image", durable=True)

        logger.info("Vision worker connected to RabbitMQ. Waiting for messages...")

        async with queue.iterator() as q:
            async for message in q:
                await handle_message(message, channel)


if __name__ == "__main__":
    asyncio.run(main())