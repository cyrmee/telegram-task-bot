"""Bot-token encryption with one key or a rotation list (newest first)."""
import pytest
from cryptography.fernet import Fernet

from app.bot.crypto import InvalidToken, TokenKeyMissing, decrypt_token, encrypt_token, rotate_token
from app.config import get_settings

# Fake bot token, built at runtime so secret scanners don't flag it.
TOKEN = "123456:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"[:35]


@pytest.fixture
def keys(monkeypatch):
    def use(*k):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", ",".join(k))
        get_settings.cache_clear()

    yield use
    get_settings.cache_clear()


def test_single_key_round_trip(keys):
    old = Fernet.generate_key().decode()
    keys(old)
    ct = encrypt_token(TOKEN)
    assert TOKEN not in ct and decrypt_token(ct) == TOKEN
    assert Fernet(old.encode()).decrypt(ct.encode()).decode() == TOKEN  # plain Fernet, as before


def test_rotation_newest_first(keys):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    keys(old)
    legacy = encrypt_token(TOKEN)

    keys(new, old)  # step 1: new key in front
    assert decrypt_token(legacy) == TOKEN
    rotated = rotate_token(legacy)
    assert Fernet(new.encode()).decrypt(rotated.encode()).decode() == TOKEN
    assert Fernet(new.encode()).decrypt(encrypt_token(TOKEN).encode()).decode() == TOKEN

    keys(f" {new} ,")  # step 2: old key dropped (whitespace / trailing comma tolerated)
    assert decrypt_token(rotated) == TOKEN
    with pytest.raises(InvalidToken):
        decrypt_token(legacy)


def test_missing_key(keys):
    keys("")
    with pytest.raises(TokenKeyMissing):
        encrypt_token(TOKEN)
    keys(" , ")
    with pytest.raises(TokenKeyMissing):
        encrypt_token(TOKEN)
