"""Encrypting BotFather tokens at rest (workspace_bots.token_encrypted)."""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenKeyMissing(RuntimeError):
    pass


@lru_cache
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode())


def _current() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenKeyMissing("TOKEN_ENCRYPTION_KEY is not set")
    return _fernet(key)


def encrypt_token(token: str) -> str:
    return _current().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Raises cryptography.fernet.InvalidToken if the key changed."""
    return _current().decrypt(ciphertext.encode()).decode()


__all__ = ["InvalidToken", "TokenKeyMissing", "decrypt_token", "encrypt_token"]
