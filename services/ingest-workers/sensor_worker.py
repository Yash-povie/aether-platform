"""
sensor_worker.py — Real pandas/scipy CSV time-series analysis worker.

Consumes from: ingest.csv (RabbitMQ)
Publishes to:  agent (topic exchange) -> agent.ready
Stores:        MinIO processed/<job_id>/sensor_artifact.json
DB:            job_artifacts row (modality=csv, worker=sensor_worker)
"""

import asyncio
import io
import json
import logging
import os
import uuid

import aio_pika
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from minio import Minio
from scipy import stats
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
RABBITMQ_URL   = os.getenv("RABBITMQ_URL",    "amqp://guest:guest@localhost:5672/")
DATABASE_URL   = os.getenv("DATABASE_URL",    "postgresql+asyncpg://aether:aether@localhost:5432/aether")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin")

PROCESSED_BUCKET  = "processed"
ZSCORE_THRESHOLD  = float(os.getenv("SENSOR_ZSCORE_THRESHOLD", "3.0"))

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_bucket() -> None:
    if not minio_client.bucket_exists(PROCESSED_BUCKET):
        minio_client.make_bucket(PROCESSED_BUCKET)


def _safe_float(value) -> float | None:
    """Convert numpy scalar to Python float, guarding against NaN/Inf."""
    try:
        f = float(value)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_timeseries(data: pd.DataFrame) -> dict:
    """
    Perform statistical analysis and z-score anomaly detection on all numeric
    columns of a DataFrame.
    """
    numeric_cols = data.select_dtypes(include=[np.number]).columns.tolist()

    per_column: dict[str, dict] = {}
    anomalies:  list[dict]      = []

    for col in numeric_cols:
        series = data[col].dropna()
        if len(series) < 3:
            logger.debug("Column '%s' has fewer than 3 non-null values — skipping", col)
            continue

        col_stats: dict = {
            "mean":    _safe_float(series.mean()),
            "std":     _safe_float(series.std()),
            "min":     _safe_float(series.min()),
            "max":     _safe_float(series.max()),
            "median":  _safe_float(series.median()),
            "count":   int(len(series)),
            "missing": int(data[col].isna().sum()),
        }

        # Trend: simple linear regression slope (positive/negative/flat)
        try:
            slope, intercept, r_value, p_value, _ = stats.linregress(
                np.arange(len(series)), series.values
            )
            col_stats["trend"] = {
                "slope":    _safe_float(slope),
                "r_squared": _safe_float(r_value ** 2),
                "p_value":  _safe_float(p_value),
                "direction": "increasing" if slope > 0 else "decreasing" if slope < 0 else "flat",
            }
        except Exception as exc:
            logger.debug("Trend computation failed for column '%s': %s", col, exc)

        # Percentiles
        try:
            col_stats["percentiles"] = {
                "p25": _safe_float(series.quantile(0.25)),
                "p50": _safe_float(series.quantile(0.50)),
                "p75": _safe_float(series.quantile(0.75)),
                "p95": _safe_float(series.quantile(0.95)),
                "p99": _safe_float(series.quantile(0.99)),
            }
        except Exception:
            pass

        # Z-score anomaly detection
        try:
            z_scores        = np.abs(stats.zscore(series))
            anomaly_mask    = z_scores > ZSCORE_THRESHOLD
            anomaly_indices = np.where(anomaly_mask)[0]

            if len(anomaly_indices) > 0:
                max_z = float(z_scores.max())
                anomalies.append({
                    "column":          col,
                    "anomaly_count":   int(len(anomaly_indices)),
                    "anomaly_indices": anomaly_indices.tolist()[:20],  # cap at 20
                    "max_z_score":     round(max_z, 4),
                    "severity":        "critical" if max_z > 5 else
                                       "high"     if max_z > 4 else
                                       "medium",
                    "anomaly_values":  [
                        _safe_float(series.iloc[i]) for i in anomaly_indices[:5]
                    ],
                })
        except Exception as exc:
            logger.warning("Z-score computation failed for column '%s': %s", col, exc)

        per_column[col] = col_stats

    # Correlation matrix for numeric columns (only when >1 column)
    correlation: dict = {}
    if len(numeric_cols) > 1:
        try:
            corr_df = data[numeric_cols].corr().fillna(0)
            correlation = {
                col: {
                    other: round(float(corr_df.loc[col, other]), 4)
                    for other in numeric_cols
                    if other != col
                }
                for col in numeric_cols
            }
        except Exception as exc:
            logger.debug("Correlation computation failed: %s", exc)

    return {
        "num_rows":       int(len(data)),
        "num_columns":    int(len(data.columns)),
        "columns":        data.columns.tolist(),
        "numeric_columns": numeric_cols,
        "statistics":     per_column,
        "anomalies":      anomalies,
        "anomaly_count":  len(anomalies),
        "correlation":    correlation,
        "has_anomalies":  len(anomalies) > 0,
    }


def _load_csv(csv_bytes: bytes) -> pd.DataFrame:
    """
    Try to parse the CSV with sensible defaults.
    Attempts comma then semicolon and tab separators on failure.
    """
    for sep in (",", ";", "\t"):
        try:
            df = pd.read_csv(io.BytesIO(csv_bytes), sep=sep, engine="python",
                             on_bad_lines="skip")
            if len(df.columns) > 1 or sep == "\t":
                return df
        except Exception:
            pass
    # Last resort: single-column
    return pd.read_csv(io.BytesIO(csv_bytes), on_bad_lines="skip")

# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def process_csv(job_id: str, file_ref: str) -> str:
    """Download CSV from MinIO, analyse, write result. Returns content_uri."""
    logger.info("Downloading CSV for job %s from %s", job_id, file_ref)
    bucket, obj_name = file_ref.replace("minio://", "").split("/", 1)
    response = minio_client.get_object(bucket, obj_name)
    try:
        csv_bytes = response.read()
    finally:
        response.close()
        response.release_conn()

    logger.info("Parsing CSV (%d bytes) for job %s", len(csv_bytes), job_id)

    # Run the CPU-bound pandas work in the default thread pool so the
    # event loop stays unblocked
    loop   = asyncio.get_running_loop()
    data   = await loop.run_in_executor(None, _load_csv, csv_bytes)
    result = await loop.run_in_executor(None, analyze_timeseries, data)

    logger.info("Sensor analysis for job %s: %d rows, %d numeric cols, %d anomalies",
                job_id,
                result["num_rows"],
                len(result["numeric_columns"]),
                result["anomaly_count"])

    ensure_bucket()
    result_key  = f"{job_id}/sensor_artifact.json"
    output_data = json.dumps(result, indent=2).encode("utf-8")
    minio_client.put_object(
        PROCESSED_BUCKET, result_key,
        io.BytesIO(output_data), len(output_data),
        content_type="application/json",
    )
    return f"minio://{PROCESSED_BUCKET}/{result_key}"


async def _write_artifact(job_id: str, content_uri: str) -> None:
    artifact = JobArtifact(
        job_id=uuid.UUID(job_id),
        modality="csv",
        worker="sensor_worker",
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
            body=json.dumps({"job_id": job_id, "modality": "csv"}).encode(),
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
            logger.info("Sensor worker received job %s", job_id)

            content_uri = await process_csv(job_id, file_ref)
            await _write_artifact(job_id, content_uri)
            await _publish_ready(channel, job_id)

            logger.info("Sensor worker completed job %s -> %s", job_id, content_uri)
        except KeyError as exc:
            logger.error("Malformed message (missing key %s): %s", exc, message.body[:200])
        except Exception:
            logger.exception("Sensor worker failed for message: %s", message.body[:200])
            raise

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Sensor worker starting...")

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("ingest.csv", durable=True)

        logger.info("Sensor worker connected to RabbitMQ. Waiting for messages...")

        async with queue.iterator() as q:
            async for message in q:
                await handle_message(message, channel)


if __name__ == "__main__":
    asyncio.run(main())