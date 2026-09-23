"""Shared FastAPI dependencies."""

from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEV_USER_ID, settings
from app.db import get_session
from app.logging import get_logger

logger = get_logger(__name__)

# auto_error=False: a missing header may simply mean the dev user.
_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    """The issuer's public keys, fetched, cached and chosen by the token's `kid`."""
    return jwt.PyJWKClient(settings.jwks_url, cache_keys=True)


def _unauthorized(reason: str) -> HTTPException:
    logger.info("auth rejected", reason=reason)
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Who is making this request: the auth seam.

    With no issuer configured, a constant dev user; with one, a verified bearer
    token's `sub`. See AGENTS.md > Authentication and authorization.
    """
    if not settings.auth_enabled:
        return DEV_USER_ID

    if credentials is None:
        raise _unauthorized("no_bearer_token")

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            # Named explicitly: trusting the token's own `alg` is the algorithm-confusion bug.
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience or None,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Logged, not returned: the reason helps us and would help an attacker.
        raise _unauthorized(type(exc).__name__) from exc

    subject = claims.get("sub")
    if not subject:
        raise _unauthorized("no_subject")
    return str(subject)


CurrentUser = Annotated[str, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
