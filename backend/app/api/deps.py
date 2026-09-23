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

# auto_error=False so a missing header reaches the code below, which can decide
# whether that is a 401 or simply the dev user.
_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    """The provider's public keys.

    PyJWKClient handles fetching, caching, and picking the key matching the
    token's `kid` — which matters because providers rotate keys without warning
    and a pinned key is an outage with a date on it.
    """
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
    """Who is making this request.

    **The auth seam.** With no issuer configured this returns a constant and the
    app runs with no accounts at all; configure one and the same function starts
    verifying a bearer token against that provider's JWKS. Nothing downstream
    changes either way, because everything downstream only ever wanted a user id.

    The token's `sub` becomes that id, and anything scoped to a user filters on
    it in the WHERE clause rather than checking afterwards — which is the
    property that makes turning this on safe rather than the beginning of an
    audit.
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
            # Named explicitly. Accepting whatever the token asks for is the
            # algorithm-confusion bug — a token signed with the public key as an
            # HMAC secret verifies happily against a permissive decoder.
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience or None,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Deliberately not echoed to the client: the distinction between
        # "expired", "wrong audience" and "bad signature" is useful to us in a
        # log and useful to an attacker in a response body.
        raise _unauthorized(type(exc).__name__) from exc

    subject = claims.get("sub")
    if not subject:
        raise _unauthorized("no_subject")
    return str(subject)


CurrentUser = Annotated[str, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
