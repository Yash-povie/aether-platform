import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "RS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))


# ---------------------------------------------------------------------------
# Key loaders
# ---------------------------------------------------------------------------

def _load_key(env_var: str, path_var: str) -> str | None:
    """
    Try to load a PEM key from:
      1. An inline PEM string in *env_var*
      2. A file path specified in *path_var*
    Returns None if neither is configured.
    """
    inline = os.getenv(env_var, "")
    if inline and inline.strip().startswith("-----"):
        # Support \\n-escaped newlines commonly stored in env vars
        return inline.replace("\\n", "\n")

    path = os.getenv(path_var, "")
    if path and os.path.isfile(path):
        with open(path, "r") as fh:
            return fh.read()

    return None


def get_private_key() -> str:
    """Return the RS256 private key PEM, raising RuntimeError if absent."""
    key = _load_key("JWT_PRIVATE_KEY", "JWT_PRIVATE_KEY_PATH")
    if not key:
        raise RuntimeError(
            "JWT private key not configured. "
            "Set JWT_PRIVATE_KEY (inline PEM) or JWT_PRIVATE_KEY_PATH (file path)."
        )
    return key


def get_public_key() -> str:
    """Return the RS256 public key PEM, raising RuntimeError if absent."""
    key = _load_key("JWT_PUBLIC_KEY", "JWT_PUBLIC_KEY_PATH")
    if not key:
        raise RuntimeError(
            "JWT public key not configured. "
            "Set JWT_PUBLIC_KEY (inline PEM) or JWT_PUBLIC_KEY_PATH (file path)."
        )
    return key


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(data: dict) -> str:
    """
    Sign *data* as an RS256 JWT.

    Adds standard claims:
      - ``iat`` — issued-at (UTC now)
      - ``exp`` — expiry (UTC now + JWT_EXPIRE_MINUTES)

    Any caller-supplied keys override these defaults, so passing ``exp``
    explicitly is respected.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
        **data,
    }
    try:
        token = jwt.encode(payload, get_private_key(), algorithm=JWT_ALGORITHM)
        return token
    except Exception as exc:
        logger.error("Token signing failed", extra={"error": str(exc)})
        raise RuntimeError(f"Could not sign JWT: {exc}") from exc


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_access_token(token: str) -> dict:
    """
    Decode and validate an RS256 JWT.

    Returns the full payload dict on success.
    Raises ``HTTPException(401)`` on any validation failure.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        public_key = get_public_key()
    except RuntimeError as exc:
        logger.error("JWT public key unavailable", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT public key not configured",
        ) from exc

    try:
        payload = jwt.decode(token, public_key, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        logger.warning("JWT decode failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Require the core identity claims
    if not payload.get("sub"):
        logger.warning("JWT missing 'sub' claim")
        raise credentials_exc

    return payload