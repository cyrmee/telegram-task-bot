"""Re-encrypt every stored bot token with the newest TOKEN_ENCRYPTION_KEY.

    TOKEN_ENCRYPTION_KEY=<new>,<old> python scripts/rotate_token_key.py --dry-run
    TOKEN_ENCRYPTION_KEY=<new>,<old> python scripts/rotate_token_key.py

All rows change in one transaction: if any token can't be decrypted with the
configured keys, nothing is written and the script exits non-zero. Runbook:
docs/security.md, "Rotating TOKEN_ENCRYPTION_KEY". Never prints tokens.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.bot.crypto import InvalidToken, rotate_token  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import WorkspaceBot  # noqa: E402


def rotate(dry_run: bool) -> tuple[int, list[str]]:
    """Returns (rows re-encrypted, workspace ids that failed)."""
    failed: list[str] = []
    rotated = 0
    with SessionLocal() as db:
        rows = db.scalars(select(WorkspaceBot).with_for_update()).all()
        for row in rows:
            try:
                row.token_encrypted = rotate_token(row.token_encrypted)
                rotated += 1
            except InvalidToken:
                failed.append(str(row.workspace_id))
        if failed or dry_run:
            db.rollback()
        else:
            db.commit()
    return rotated, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="check every token decrypts; write nothing")
    args = parser.parse_args()
    rotated, failed = rotate(args.dry_run)
    if failed:
        print(f"{len(failed)} token(s) can't be decrypted with the configured keys; nothing written.")
        print("Workspaces: " + ", ".join(failed))
        return 1
    verb = "would re-encrypt" if args.dry_run else "re-encrypted"
    print(f"{verb} {rotated} bot token(s) with the newest key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
