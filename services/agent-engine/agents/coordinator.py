import os
import json
import logging
from typing import Dict, Any

from minio import Minio

from ..schemas import PipelineState

logger = logging.getLogger(__name__)

MINIO_ENDPOINT  = os.getenv("MINIO_ENDPOINT",   "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET    = os.getenv("MINIO_BUCKET",      "aether-artifacts")
MINIO_SECURE    = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Module-level client; connection is lazy/stateless (HTTP per request).
_minio_client: Minio | None = None


def _get_minio_client() -> Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    return _minio_client


def fetch_artifact_content(content_uri: str) -> str:
    """Download an artifact from MinIO and return its text representation.

    Handles two URI schemes:
    - ``minio://<bucket>/<object_path>``  (canonical form produced by workers)
    - ``<object_path>``                   (bare object key fallback)

    Returns a UTF-8 string up to 4 000 characters so that downstream LLM
    nodes receive bounded context.  JSON payloads are pretty-printed;
    binary/opaque blobs are represented as a placeholder.
    """
    client = _get_minio_client()
    try:
        # Normalise URI → object key
        prefix = f"minio://{MINIO_BUCKET}/"
        if content_uri.startswith(prefix):
            object_key = content_uri[len(prefix):]
        elif content_uri.startswith("minio://"):
            # minio://<different-bucket>/<key> — strip up to first slash after host
            parts = content_uri[len("minio://"):].split("/", 1)
            object_key = parts[1] if len(parts) == 2 else parts[0]
        else:
            object_key = content_uri

        response = client.get_object(MINIO_BUCKET, object_key)
        try:
            raw = response.read()
        finally:
            response.close()
            response.release_conn()

        # Attempt JSON parse for structured payloads (transcription, OCR, etc.)
        try:
            parsed = json.loads(raw)
            return json.dumps(parsed, indent=2, ensure_ascii=False)[:4000]
        except (json.JSONDecodeError, ValueError):
            pass

        # Plain text fallback
        try:
            return raw.decode("utf-8", errors="replace")[:4000]
        except Exception:
            return f"[Binary content — {len(raw)} bytes, not displayable as text]"

    except Exception as exc:
        logger.warning(
            "[Coordinator] Could not fetch artifact %s: %s",
            content_uri,
            exc,
            exc_info=False,
        )
        return f"[Content unavailable: {exc}]"


def coordinator_node(state: PipelineState) -> Dict[str, Any]:
    """Load artifact content from MinIO and prepare the pipeline context.

    Reads the ``artifacts`` list already attached to state (populated either
    directly from the API gateway or by the worker from PostgreSQL), downloads
    the textual content for each artifact from MinIO, and writes a concise
    ``task_description`` that downstream agents can rely on.
    """
    job_id = state.get("job_id", "<unknown>")
    logger.info("[Coordinator] Starting content-load phase for job %s", job_id)

    raw_artifacts: list[Dict[str, Any]] = state.get("artifacts", [])
    loaded_artifacts: list[Dict[str, Any]] = []
    failed = 0

    for art in raw_artifacts:
        content_uri = art.get("content_uri", "")
        if content_uri:
            content = fetch_artifact_content(content_uri)
            ok = content and not content.startswith("[Content unavailable")
        else:
            content = "[No content URI provided]"
            ok = False

        if not ok:
            failed += 1

        loaded_artifacts.append(
            {
                **art,
                "content": content,
                "loaded": ok,
            }
        )

    modalities = list({a.get("modality", "unknown") for a in loaded_artifacts})
    task_description = (
        f"Analyze {len(loaded_artifacts)} multimodal artifact(s) "
        f"(modalities: {', '.join(sorted(modalities)) or 'unknown'}) "
        f"for anomalies, cross-modal evidence patterns, and key intelligence findings. "
        f"Job ID: {job_id}."
    )

    logger.info(
        "[Coordinator] Job %s — %d artifact(s) loaded, %d failed. Modalities: %s",
        job_id,
        len(loaded_artifacts) - failed,
        failed,
        modalities,
    )

    audit_entry = {
        "step": "coordinator",
        "artifacts_total": len(loaded_artifacts),
        "artifacts_loaded": len(loaded_artifacts) - failed,
        "artifacts_failed": failed,
        "modalities": modalities,
    }

    return {
        "artifacts": loaded_artifacts,
        "task_description": task_description,
        "audit_trail": state.get("audit_trail", []) + [audit_entry],
    }