"""scripts/rotate_token_key.py: all-or-nothing re-encryption with the newest key."""
import uuid

import pytest
from cryptography.fernet import Fernet

from app.bot import crypto
from app.config import get_settings
from app.models import WorkspaceBot
from scripts.rotate_token_key import rotate
from tests.factories import make_workspace

OLD, NEW, OTHER = (Fernet.generate_key().decode() for _ in range(3))


@pytest.fixture
def keys(monkeypatch):
    def use(value: str):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", value)
        get_settings.cache_clear()
        crypto._fernet.cache_clear()

    yield use
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


def add_bot(db, bot_id: int, ciphertext: str) -> uuid.UUID:
    ws = make_workspace(db, owner=f"owner{bot_id}")
    db.add(WorkspaceBot(workspace_id=ws.id, telegram_bot_id=bot_id, username=f"b{bot_id}", token_encrypted=ciphertext, webhook_secret="s"))
    db.commit()
    return ws.id


def test_rotates_to_newest_key(db_session, keys):
    keys(OLD)
    ws = add_bot(db_session, 1, crypto.encrypt_token("123:secret-token"))
    keys(f"{NEW},{OLD}")
    assert rotate(dry_run=True) == (1, [])
    db_session.expire_all()
    with pytest.raises(Exception):
        Fernet(NEW.encode()).decrypt(db_session.get(WorkspaceBot, ws).token_encrypted.encode())  # dry run wrote nothing
    assert rotate(dry_run=False) == (1, [])
    db_session.expire_all()
    ciphertext = db_session.get(WorkspaceBot, ws).token_encrypted
    assert Fernet(NEW.encode()).decrypt(ciphertext.encode()) == b"123:secret-token"
    keys(NEW)  # old key can now be dropped
    assert crypto.decrypt_token(ciphertext) == "123:secret-token"


def test_all_or_nothing_when_a_token_is_undecryptable(db_session, keys):
    keys(OLD)
    good = add_bot(db_session, 2, crypto.encrypt_token("123:a"))
    before = db_session.get(WorkspaceBot, good).token_encrypted
    keys(OTHER)
    stray = add_bot(db_session, 3, crypto.encrypt_token("123:b"))
    keys(f"{NEW},{OLD}")
    rotated, failed = rotate(dry_run=False)
    assert failed == [str(stray)]
    db_session.expire_all()
    assert db_session.get(WorkspaceBot, good).token_encrypted == before
