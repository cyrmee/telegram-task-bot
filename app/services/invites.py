"""Invite links and redeeming them (the bot's /start <token>, B2)."""
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, cast

from sqlalchemy import func, null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.api.errors import ApiError
from app.models import BotStatus, Invite, Member, MemberRole, MemberStatus, TelegramUser, Workspace, WorkspaceBot
from app.notifications import wake_member
from app.services.views import member_summary

LINK_TTL = timedelta(hours=1)

Outcome = Literal["joined", "linked", "reactivated", "already_member", "expired", "revoked", "used_up", "invalid"]


@dataclass(frozen=True)
class TelegramIdentity:
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None


@dataclass
class RedeemResult:
    outcome: Outcome
    member: Optional[Member] = None
    workspace_name: Optional[str] = None


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


def invite_state(invite: Invite, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if invite.revoked_at is not None:
        return "revoked"
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        return "used_up"
    if invite.expires_at <= now:
        return "expired"
    return "active"


def invite_out(invite: Invite, bot_username: Optional[str]) -> dict:
    return {
        "id": invite.id,
        "token": invite.token,
        "url": f"https://t.me/{bot_username}?start={invite.token}" if bot_username else "",
        "state": invite_state(invite),
        "expires_at": invite.expires_at,
        "max_uses": invite.max_uses,
        "use_count": invite.use_count,
        "revoked_at": invite.revoked_at,
        "created_by": member_summary(invite.created_by) if invite.created_by_member_id else None,
        "created_at": invite.created_at,
    }


def _bot_username(db: Session, workspace_id: uuid.UUID) -> Optional[str]:
    return db.scalar(
        select(WorkspaceBot.username).where(WorkspaceBot.workspace_id == workspace_id, WorkspaceBot.status == BotStatus.ACTIVE)
    )


def list_invites(db: Session, workspace_id: uuid.UUID, states: Optional[list[str]] = None) -> list[dict]:
    wanted = set(states or ["active"])
    invites = db.scalars(
        select(Invite)
        .options(selectinload(Invite.created_by).selectinload(Member.telegram_user))
        .where(Invite.workspace_id == workspace_id, Invite.member_id.is_(None))  # self-link invites aren't team invites
        .order_by(Invite.created_at.desc())
    )
    username = _bot_username(db, workspace_id)
    return [invite_out(i, username) for i in invites if invite_state(i) in wanted]


def create_invite(db: Session, actor: Member, expires_in_hours: int, max_uses: Optional[int]) -> dict:
    bot = active_bot(db, actor.workspace_id)
    invite = Invite(
        workspace_id=actor.workspace_id,
        token=new_token(),
        role=MemberRole.MEMBER,
        created_by_member_id=actor.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
        # Plain None would be left out of the INSERT, and the column default (1) would apply.
        max_uses=max_uses if max_uses is not None else null(),
    )
    db.add(invite)
    db.commit()
    invite = db.scalar(
        select(Invite).options(selectinload(Invite.created_by).selectinload(Member.telegram_user)).where(Invite.id == invite.id)
    )
    return invite_out(invite, bot.username)


def revoke_invite(db: Session, actor: Member, invite_id: uuid.UUID) -> None:
    invite = db.scalar(select(Invite).where(Invite.workspace_id == actor.workspace_id, Invite.id == invite_id))
    if invite is None:
        raise ApiError(404, "not_found", "Invite not found.")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(timezone.utc)
        db.commit()


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


# --- redeeming (called by the bot) ---------------------------------------------------

def _upsert_telegram_user(db: Session, tg: TelegramIdentity) -> None:
    values = dict(
        telegram_user_id=tg.id, username=tg.username, first_name=tg.first_name,
        last_name=tg.last_name, language_code=tg.language_code,
    )
    db.execute(
        insert(TelegramUser)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[TelegramUser.telegram_user_id],
            set_={k: v for k, v in values.items() if k != "telegram_user_id"} | {"updated_at": func.now()},
        )
    )


def _activate(member: Member, tg: TelegramIdentity, chat_id: int) -> None:
    member.telegram_user_id = tg.id
    member.private_chat_id = chat_id
    member.status = MemberStatus.ACTIVE
    member.removed_at = None
    member.joined_at = member.joined_at or datetime.now(timezone.utc)


def _finish(db: Session, outcome: Outcome, member: Member, workspace: Workspace, invite: Optional[Invite]) -> RedeemResult:
    if invite is not None and outcome in ("joined", "linked", "reactivated"):
        invite.use_count += 1
    db.flush()
    wake_member(db, member.id)  # DMs queued before they linked go out now
    db.commit()
    return RedeemResult(outcome, member, workspace.name)


def redeem_invite(db: Session, workspace_id: uuid.UUID, token: str, tg: TelegramIdentity, chat_id: int) -> RedeemResult:
    """Handle /start <token> in the workspace's bot. Commits. Doesn't emit events:
    on joined/linked/reactivated the caller emits member.joined/member.updated."""
    workspace = db.get(Workspace, workspace_id)
    invite = db.scalar(
        select(Invite)
        .where(Invite.workspace_id == workspace_id, Invite.token == token)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invite is None or workspace is None:
        return RedeemResult("invalid", workspace_name=workspace.name if workspace else None)
    state = invite_state(invite)
    if state != "active":
        return RedeemResult(cast(Outcome, state), workspace_name=workspace.name)

    _upsert_telegram_user(db, tg)
    by_telegram = db.scalar(
        select(Member)
        .where(Member.workspace_id == workspace_id, Member.telegram_user_id == tg.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if invite.member_id is not None:  # "link my Telegram" invite
        target = db.scalar(
            select(Member).where(Member.id == invite.member_id).with_for_update().execution_options(populate_existing=True)
        )
        if target is None or target.status == MemberStatus.REMOVED:
            return RedeemResult("revoked", workspace_name=workspace.name)
        if by_telegram is not None and by_telegram.id != target.id:
            # This Telegram account is already a separate member here; don't merge identities silently.
            return RedeemResult("already_member", by_telegram, workspace.name)
        if target.telegram_user_id == tg.id:
            return RedeemResult("already_member", target, workspace.name)
        _activate(target, tg, chat_id)
        return _finish(db, "linked", target, workspace, invite)

    if by_telegram is not None:
        if by_telegram.status == MemberStatus.REMOVED:
            # Rejoining through a team invite grants the invite's role, never the old one
            # (a removed manager must not get the dashboard back this way).
            by_telegram.role = invite.role
            by_telegram.invite_id = invite.id
            _activate(by_telegram, tg, chat_id)
            return _finish(db, "reactivated", by_telegram, workspace, invite)
        by_telegram.private_chat_id = chat_id
        if by_telegram.status == MemberStatus.UNREACHABLE:
            by_telegram.status = MemberStatus.ACTIVE  # they've started the bot again
        return _finish(db, "already_member", by_telegram, workspace, None)

    pending = _pending_by_username(db, workspace_id, tg)
    if pending is not None:
        _activate(pending, tg, chat_id)
        pending.invite_id = invite.id
        return _finish(db, "joined", pending, workspace, invite)

    member = Member(workspace_id=workspace_id, role=invite.role, invite_id=invite.id)
    _activate(member, tg, chat_id)
    db.add(member)
    return _finish(db, "joined", member, workspace, invite)


def _pending_by_username(db: Session, workspace_id: uuid.UUID, tg: TelegramIdentity) -> Optional[Member]:
    if not tg.username:
        return None
    return db.scalar(
        select(Member)
        .where(
            Member.workspace_id == workspace_id,
            Member.status == MemberStatus.PENDING,
            Member.telegram_user_id.is_(None),
            func.lower(Member.invited_username) == tg.username.lower(),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def match_pending_by_username(db: Session, workspace_id: uuid.UUID, tg: TelegramIdentity, chat_id: int) -> Optional[RedeemResult]:
    """/start without a token: a member invited by @username joins on their first /start."""
    pending = _pending_by_username(db, workspace_id, tg)
    if pending is None:
        return None
    _upsert_telegram_user(db, tg)
    _activate(pending, tg, chat_id)
    return _finish(db, "joined", pending, db.get(Workspace, workspace_id), None)
