import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt_handler import verify_access_token

logger = logging.getLogger(__name__)

# Use auto_error=False so we can return a clean 401 (not FastAPI's default 403)
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency — extracts and validates the Bearer JWT.

    Returns the decoded token payload (dict with at minimum ``sub``,
    ``user_id``, ``org_id``, ``role``).

    Raises ``HTTPException(401)`` when the header is absent or the token
    is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = verify_access_token(credentials.credentials)
    logger.debug(
        "Authenticated request",
        extra={"user_id": user.get("user_id"), "org_id": user.get("org_id")},
    )
    return user


def require_role(*roles: str):
    """
    Dependency factory — gate an endpoint to specific role(s).

    Usage::

        @app.get("/admin-only")
        async def handler(user = Depends(require_role("admin"))):
            ...

        @app.get("/analyst-or-admin")
        async def handler(user = Depends(require_role("analyst", "admin"))):
            ...

    Raises ``HTTPException(403)`` when the authenticated user's role is not
    in the allowed set.
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            logger.warning(
                "RBAC denied",
                extra={
                    "user_id": user.get("user_id"),
                    "user_role": user.get("role"),
                    "required_roles": roles,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.get('role')}' is not permitted. "
                    f"Required: {list(roles)}"
                ),
            )
        return user

    return _check