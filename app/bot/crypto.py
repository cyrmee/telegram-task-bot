"""Encrypting BotFather tokens at rest (workspace_bots.token_encrypted).

TOKEN_ENCRYPTION_KEY holds one Fernet key, or several comma-separated, newest
first. New ciphertext uses the newest key; any listed key decrypts. To rotate:
put a new key in front, deploy, re-encrypt with rotate_token()
(scripts/rotate_token_key.py), drop the old key, deploy (docs/security.md).
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings


class TokenKeyMissing(RuntimeError):
    pass


@lru_cache
def _fernet(keys: str) -> MultiFernet:
    parts = [k.strip() for k in keys.split(",") if k.strip()]
    if not parts:
        raise TokenKeyMissing("TOKEN_ENCRYPTION_KEY is not set")
    return MultiFernet([Fernet(k.encode()) for k in parts])


def _current() -> MultiFernet:
    keys = get_settings().token_encryption_key
    if not keys.strip():
        raise TokenKeyMissing("TOKEN_ENCRYPTION_KEY is not set")
    return _fernet(keys)


def encrypt_token(token: str) -> str:
    return _current().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Raises cryptography.fernet.InvalidToken if no configured key matches."""
    return _current().decrypt(ciphertext.encode()).decode()


def rotate_token(ciphertext: str) -> str:
    """Re-encrypt with the newest key (accepts ciphertext from any configured key)."""
    return _current().rotate(ciphertext.encode()).decode()


__all__ = ["InvalidToken", "TokenKeyMissing", "decrypt_token", "encrypt_token", "rotate_token"]
