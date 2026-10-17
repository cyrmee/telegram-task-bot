"""Verifying the dashboard's better-auth JWTs (docs/api-v1.md §2).

Shared by the REST dependency (app/api/deps.py) and the Socket.IO connect
handler (app/realtime.py).
"""
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient

from app.config import get_settings

logger = logging.getLogger(__name__)

# Asymmetric only: an attacker must never be able to pick HS256/none.
ALLOWED_ALGORITHMS = ["EdDSA", "ES256", "ES512", "RS256", "PS256"]


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class AuthUser:
    id: str  # better-auth user id (JWT `sub`)
    email: Optional[str] = None
    name: Optional[str] = None
    image: Optional[str] = None


_jwks_client: Optional[PyJWKClient] = None


def jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        s = get_settings()
        # Keys are cached; an unknown `kid` triggers one refetch (key rotation).
        _jwks_client = PyJWKClient(s.auth_jwks_url, cache_keys=True, lifespan=s.auth_jwks_cache_seconds, timeout=5)
    return _jwks_client


def set_jwks_client(client: Optional[PyJWKClient]) -> None:
    """Tests swap in a client backed by a local key set."""
    global _jwks_client
    _jwks_client = client


def verify_token(token: str) -> AuthUser:
    """Blocking (may fetch JWKS); call from a threadpool in async code."""
    s = get_settings()
    try:
        signing_key = jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=s.auth_jwt_audience,
            issuer=s.auth_jwt_issuer,
            leeway=s.auth_leeway_seconds,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("JWKS fetch failed: %s", exc)
        raise InvalidToken("Could not verify token") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    return AuthUser(id=str(claims["sub"]), email=claims.get("email"), name=claims.get("name"), image=claims.get("image"))


def telegram_secret_ok(received: Optional[str], expected: str) -> bool:
    """Check X-Telegram-Bot-Api-Secret-Token in constant time (B1's webhook route)."""
    return received is not None and hmac.compare_digest(received.encode(), expected.encode())
