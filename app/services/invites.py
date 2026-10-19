"""Invite links. redeem_invite() (A5, used by the bot's /start in B2) lands here too."""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.models import BotStatus, Invite, Member, WorkspaceBot

LINK_TTL = timedelta(hours=1)


def new_token() -> str:
    # 32 chars of [A-Za-z0-9_-]: fits Telegram's 64-char start parameter.
    return secrets.token_urlsafe(24)


def active_bot(db: Session, workspace_id) -> WorkspaceBot:
    bot = db.scalar(
        select(WorkspaceBot).where(WorkspaceBot.workspace_id == workspace_id, WorkspaceBot.status == BotStatus.ACTIVE)
    )
    if bot is None:
        raise ApiError(409, "bot_not_connected", "Connect your Telegram bot first.")
    return bot


def deep_link(bot: WorkspaceBot, token: str) -> str:
    return f"https://t.me/{bot.username}?start={token}"


def create_telegram_link(db: Session, member: Member) -> dict:
    """Single-use, short-lived invite that attaches Telegram to `member` itself."""
    if member.telegram_user_id is not None:
        raise ApiError(409, "telegram_already_linked", "Your Telegram account is already linked.")
    bot = active_bot(db, member.workspace_id)
    invite = Invite(
        workspace_id=member.workspace_id,
        token=new_token(),
        role=member.role,
        member_id=member.id,
        created_by_member_id=member.id,
        max_uses=1,
        expires_at=datetime.now(timezone.utc) + LINK_TTL,
    )
    db.add(invite)
    db.commit()
    return {"url": deep_link(bot, invite.token), "expires_at": invite.expires_at}
