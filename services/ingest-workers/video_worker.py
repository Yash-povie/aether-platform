"""
video_worker.py — OpenCV frame extraction + Anthropic Vision analysis.

Consumes from: ingest.video (RabbitMQ)
Publishes to:  agent (topic exchange) -> agent.ready
Stores:        MinIO processed/<job_id>/video_artifact.json
               MinIO processed/<job_id>/frames/<n>.jpg  (keyframes)
DB:            job_artifacts row (modality=video, worker=video_worker)
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid

import aio_pika
import anthropic
import cv2
import numpy as np
from dotenv import load_dotenv
from minio import Minio
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
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY")
MAX_FRAMES        = int(os.getenv("VIDEO_MAX_FRAMES", "5"))

PROCESSED_BUCKET = "processed"

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False
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


def extract_keyframes(
    video_path: str, max_frames: int = MAX_FRAMES
) -> tuple[list[np.ndarray], float, float]:
    """
    Sample `max_frames` evenly across the video.
    Returns (frames, duration_seconds, fps).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video file: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0
    duration     = total_frames / fps if fps > 0 else 0.0

    n_frames     = min(max_frames, total_frames) if total_frames > 0 else 0
    if n_frames == 0:
        cap.release()
        return [], duration, fps

    indices = np.linspace(0, max(0, total_frames - 1), n_frames, dtype=int)
    frames  = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, frame = cap.read()
        if ret:
            frames.append(frame)

    cap.release()
    logger.info("Extracted %d keyframes from %d total (duration=%.1fs, fps=%.1f)",
                len(frames), total_frames, duration, fps)
    return frames, duration, fps


def frame_to_b64(frame: np.ndarray) -> str:
    """Encode a BGR OpenCV frame as base64 JPEG."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("Failed to encode frame as JPEG")
    return base64.standard_b64encode(buf.tobytes()).decode("utf-8")


def _extract_json(text: str) -> dict:
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


def analyse_frame_sync(b64_frame: str, frame_idx: int) -> dict:
    """Synchronous call to Anthropic — run in executor from async context."""
    message = anthropic_client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64_frame,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"This is frame #{frame_idx} extracted from a video.\n"
                            "Analyse it for anomalies, objects, and defects.\n"
                            "Return valid JSON only:\n"
                            "{\n"
                            '  "captions": "...",\n'
                            '  "anomaly_flags": [],\n'
                            '  "objects_detected": [],\n'
                            '  "quality_score": 0.0-1.0,\n'
                            '  "confidence": 0.0-1.0\n'
                            "}"
                        ),
                    },
                ],
            }
        ],
    )
    return _extract_json(message.content[0].text)


def try_extract_audio(video_path: str, output_dir: str) -> str | None:
    """
    Attempt to extract audio with ffmpeg.
    Returns path to audio file or None if ffmpeg is unavailable / no audio track.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found on PATH — skipping audio extraction")
        return None
    audio_path = os.path.join(output_dir, "audio.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "mp3", audio_path],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        logger.warning("ffmpeg audio extraction failed (no audio track?): %s",
                       result.stderr[-300:])
        return None
    return audio_path


def aggregate_frame_analyses(frame_results: list[dict]) -> dict:
    """Merge per-frame analyses into a single video-level result."""
    if not frame_results:
        return {"captions": "No frames analysed", "anomaly_flags": [],
                "objects_detected": [], "quality_score": 0.0, "confidence": 0.0}

    all_anomalies = []
    all_objects   = []
    quality_scores = []
    confidences    = []
    captions       = []

    for r in frame_results:
        all_anomalies.extend(r.get("anomaly_flags", []))
        all_objects.extend(r.get("objects_detected", []))
        q = r.get("quality_score")
        if isinstance(q, (int, float)):
            quality_scores.append(float(q))
        c = r.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))
        cap = r.get("captions", "")
        if cap:
            captions.append(cap)

    return {
        "captions":         " | ".join(captions),
        "anomaly_flags":    list(dict.fromkeys(all_anomalies)),   # deduplicate, preserve order
        "objects_detected": list(dict.fromkeys(all_objects)),
        "quality_score":    round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else 0.0,
        "confidence":       round(sum(confidences) / len(confidences), 4)       if confidences    else 0.0,
    }

# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def process_video(job_id: str, file_ref: str) -> str:
    """Download, extract frames, analyse, store result. Returns content_uri."""
    logger.info("Downloading video for job %s from %s", job_id, file_ref)
    bucket, obj_name = file_ref.replace("minio://", "").split("/", 1)
    response = minio_client.get_object(bucket, obj_name)
    try:
        video_bytes = response.read()
    finally:
        response.close()
        response.release_conn()

    # Write to temp file — OpenCV needs a file path, not a buffer
    tmp_dir = tempfile.mkdtemp(prefix="aether_video_")
    try:
        video_path = os.path.join(tmp_dir, "input.mp4")
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        frames, duration, fps = extract_keyframes(video_path, MAX_FRAMES)

        # Persist keyframes to MinIO
        ensure_bucket()
        frame_uris = []
        for i, frame in enumerate(frames):
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                frame_key = f"{job_id}/frames/{i:04d}.jpg"
                frame_data = buf.tobytes()
                minio_client.put_object(
                    PROCESSED_BUCKET, frame_key,
                    io.BytesIO(frame_data), len(frame_data),
                    content_type="image/jpeg",
                )
                frame_uris.append(f"minio://{PROCESSED_BUCKET}/{frame_key}")

        # Analyse each frame with Anthropic Vision (run sync client in thread executor)
        loop         = asyncio.get_running_loop()
        frame_b64s   = [frame_to_b64(f) for f in frames]
        frame_tasks  = [
            loop.run_in_executor(None, analyse_frame_sync, b64, i)
            for i, b64 in enumerate(frame_b64s)
        ]
        frame_results = await asyncio.gather(*frame_tasks, return_exceptions=True)

        valid_results = []
        for i, res in enumerate(frame_results):
            if isinstance(res, Exception):
                logger.warning("Frame %d analysis failed: %s", i, res)
            else:
                valid_results.append(res)

        aggregated = aggregate_frame_analyses(valid_results)

        # Optional audio extraction
        audio_info = {}
        audio_path = try_extract_audio(video_path, tmp_dir)
        if audio_path and os.path.exists(audio_path):
            audio_key = f"{job_id}/audio.mp3"
            with open(audio_path, "rb") as af:
                audio_bytes = af.read()
            minio_client.put_object(
                PROCESSED_BUCKET, audio_key,
                io.BytesIO(audio_bytes), len(audio_bytes),
                content_type="audio/mpeg",
            )
            audio_info = {"audio_uri": f"minio://{PROCESSED_BUCKET}/{audio_key}"}

        result = {
            "frames_extracted": len(frames),
            "duration_seconds": round(duration, 2),
            "fps":              round(fps, 2),
            "frame_uris":       frame_uris,
            "analysis":         aggregated,
            **audio_info,
        }

        result_key  = f"{job_id}/video_artifact.json"
        output_data = json.dumps(result, indent=2).encode("utf-8")
        minio_client.put_object(
            PROCESSED_BUCKET, result_key,
            io.BytesIO(output_data), len(output_data),
            content_type="application/json",
        )
        return f"minio://{PROCESSED_BUCKET}/{result_key}"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _write_artifact(job_id: str, content_uri: str) -> None:
    artifact = JobArtifact(
        job_id=uuid.UUID(job_id),
        modality="video",
        worker="video_worker",
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
            body=json.dumps({"job_id": job_id, "modality": "video"}).encode(),
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
            logger.info("Video worker received job %s", job_id)

            content_uri = await process_video(job_id, file_ref)
            await _write_artifact(job_id, content_uri)
            await _publish_ready(channel, job_id)

            logger.info("Video worker completed job %s -> %s", job_id, content_uri)
        except KeyError as exc:
            logger.error("Malformed message (missing key %s): %s", exc, message.body[:200])
        except Exception:
            logger.exception("Video worker failed for message: %s", message.body[:200])
            raise

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Video worker starting...")

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("ingest.video", durable=True)

        logger.info("Video worker connected to RabbitMQ. Waiting for messages...")

        async with queue.iterator() as q:
            async for message in q:
                await handle_message(message, channel)


if __name__ == "__main__":
    asyncio.run(main())