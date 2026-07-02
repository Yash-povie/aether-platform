"""
embedding_worker.py — Real sentence-transformers embeddings + Qdrant upsert.

Consumes from: ingest.embed (RabbitMQ)
Publishes to:  agent (topic exchange) -> agent.ready
Stores:        Qdrant collection (COLLECTION_NAME) — one point per text chunk
               MinIO processed/<job_id>/embedding_artifact.json (summary)
DB:            job_artifacts row (modality=embedding, worker=embedding_worker)

Expected payload:
{
    "job_id":   "<uuid>",
    "org_id":   "<uuid>",
    "metadata": {
        "text":   "<full text content to embed>",
        "source": "pdf|image|video|csv"   // optional
    }
}
"""

import asyncio
import io
import json
import logging
import os
import uuid

import aio_pika
from dotenv import load_dotenv
from minio import Minio
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

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
RABBITMQ_URL      = os.getenv("RABBITMQ_URL",    "amqp://guest:guest@localhost:5672/")
DATABASE_URL      = os.getenv("DATABASE_URL",    "postgresql+asyncpg://aether:aether@localhost:5432/aether")
MINIO_ENDPOINT    = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
MINIO_ACCESS      = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET      = os.getenv("MINIO_SECRET_KEY", "minioadmin")
QDRANT_URL        = os.getenv("QDRANT_URL",       "http://localhost:6333")
EMBEDDING_MODEL   = os.getenv("EMBEDDING_MODEL",  "all-MiniLM-L6-v2")
COLLECTION_NAME   = os.getenv("QDRANT_COLLECTION", "aether-embeddings")
CHUNK_SIZE        = int(os.getenv("EMBED_CHUNK_SIZE", "500"))

PROCESSED_BUCKET  = "processed"
VECTOR_DIM        = 384  # all-MiniLM-L6-v2 output dimension

# ---------------------------------------------------------------------------
# Clients (module-level; model is loaded once at startup)
# ---------------------------------------------------------------------------
minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False
)

qdrant = AsyncQdrantClient(url=QDRANT_URL)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

logger.info("Loading sentence-transformers model: %s", EMBEDDING_MODEL)
_st_model = SentenceTransformer(EMBEDDING_MODEL)
logger.info("Model loaded (vector dim=%d)", VECTOR_DIM)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_bucket() -> None:
    if not minio_client.bucket_exists(PROCESSED_BUCKET):
        minio_client.make_bucket(PROCESSED_BUCKET)


async def ensure_collection() -> None:
    """Create the Qdrant collection if it does not exist yet."""
    try:
        await qdrant.get_collection(collection_name=COLLECTION_NAME)
        logger.debug("Qdrant collection '%s' already exists", COLLECTION_NAME)
    except Exception:
        logger.info("Creating Qdrant collection '%s' (dim=%d)", COLLECTION_NAME, VECTOR_DIM)
        await qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """
    Split text into non-overlapping chunks of `chunk_size` characters.
    Tries to break on sentence boundaries where possible, falls back to
    hard splits to guarantee a consistent maximum length.
    """
    if not text or not text.strip():
        return ["No text content available"]

    # Attempt sentence-aware splitting
    sentences  = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    chunks: list[str] = []
    current    = ""

    for sentence in sentences:
        probe = (current + ". " + sentence).strip() if current else sentence
        if len(probe) <= chunk_size:
            current = probe
        else:
            if current:
                chunks.append(current)
            # If a single sentence exceeds chunk_size, hard-split it
            while len(sentence) > chunk_size:
                chunks.append(sentence[:chunk_size])
                sentence = sentence[chunk_size:]
            current = sentence

    if current:
        chunks.append(current)

    return chunks if chunks else ["No text content available"]


def encode_chunks_sync(chunks: list[str]) -> list[list[float]]:
    """Run sentence-transformers encode in a thread — returns list of float lists."""
    embeddings = _st_model.encode(chunks, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()

# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def generate_and_store_embeddings(
    job_id: str, org_id: str, text_content: str, source: str
) -> dict:
    """
    Chunk text, generate embeddings, upsert to Qdrant.
    Returns a summary dict.
    """
    await ensure_collection()

    chunks = chunk_text(text_content, CHUNK_SIZE)
    logger.info("Embedding job %s: %d chunks from %d chars", job_id, len(chunks), len(text_content))

    # Run CPU-bound encoding in thread pool
    loop       = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(None, encode_chunks_sync, chunks)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=emb,
            payload={
                "job_id":      job_id,
                "org_id":      org_id,
                "chunk_index": i,
                "source":      source,
                # Store a preview (first 200 chars) to support keyword recall
                "text":        chunk[:200],
            },
        )
        for i, (emb, chunk) in enumerate(zip(embeddings, chunks))
    ]

    await qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info("Upserted %d vectors to Qdrant collection '%s' for job %s",
                len(points), COLLECTION_NAME, job_id)

    return {
        "chunks_embedded": len(chunks),
        "vector_dim":      VECTOR_DIM,
        "collection":      COLLECTION_NAME,
        "source":          source,
        "job_id":          job_id,
    }


async def process_embed(job_id: str, org_id: str, payload: dict) -> str:
    """Orchestrate embedding generation and persist summary to MinIO. Returns content_uri."""
    metadata     = payload.get("metadata", {})
    text_content = metadata.get("text", "")
    source       = metadata.get("source", "unknown")

    if not text_content.strip():
        logger.warning("Job %s has empty text content — embedding a placeholder", job_id)
        text_content = f"Job {job_id}: no text content provided"

    summary = await generate_and_store_embeddings(job_id, org_id, text_content, source)

    # Write a lightweight summary artifact to MinIO so downstream agents can
    # find the embedding metadata without hitting Qdrant directly
    ensure_bucket()
    result_key  = f"{job_id}/embedding_artifact.json"
    output_data = json.dumps(summary, indent=2).encode("utf-8")
    minio_client.put_object(
        PROCESSED_BUCKET, result_key,
        io.BytesIO(output_data), len(output_data),
        content_type="application/json",
    )
    return f"minio://{PROCESSED_BUCKET}/{result_key}"


async def _write_artifact(job_id: str, content_uri: str) -> None:
    artifact = JobArtifact(
        job_id=uuid.UUID(job_id),
        modality="embedding",
        worker="embedding_worker",
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
            body=json.dumps({"job_id": job_id, "modality": "embedding"}).encode(),
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
            payload = json.loads(message.body.decode())
            job_id  = payload["job_id"]
            org_id  = payload.get("org_id", "")
            logger.info("Embedding worker received job %s", job_id)

            content_uri = await process_embed(job_id, org_id, payload)
            await _write_artifact(job_id, content_uri)
            await _publish_ready(channel, job_id)

            logger.info("Embedding worker completed job %s -> %s", job_id, content_uri)
        except KeyError as exc:
            logger.error("Malformed message (missing key %s): %s", exc, message.body[:200])
        except Exception:
            logger.exception("Embedding worker failed for message: %s", message.body[:200])
            raise

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Embedding worker starting (model=%s, collection=%s)...",
                EMBEDDING_MODEL, COLLECTION_NAME)

    # Ensure Qdrant collection exists before accepting messages
    await ensure_collection()

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("ingest.embed", durable=True)

        logger.info("Embedding worker connected to RabbitMQ. Waiting for messages...")

        async with queue.iterator() as q:
            async for message in q:
                await handle_message(message, channel)


if __name__ == "__main__":
    asyncio.run(main())