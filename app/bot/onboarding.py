"""/start: joining a workspace through its bot (B2).

    /start <token>   redeem an invite link (t.me/<bot>?start=<token>)
    /start           match a member pre-invited by @username, or explain how to join

Redeeming goes through services.invites (Stream A). This module owns the
replies, the member.joined / member.updated events, and the DM that tells
managers someone joined.
"""
import asyncio
import html
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app import db
from app.api.v1.schemas import Member as MemberSchema
from app.models import Member, MemberRole, MemberStatus, NotificationType, Workspace
from app.notifications import enqueue, message_payload
from app.realtime import emit_workspace_event
from app.services.invites import RedeemResult, TelegramIdentity, match_pending_by_username, redeem_invite
from app.services.members import member_out
from app.services.views import display_name

FAILURES = {
    "expired": "This invite link has expired. Ask your manager for a new one.",
    "revoked": "This invite link was turned off. Ask your manager for a new one.",
    "used_up": "This invite link has already been used. Ask your manager for a new one.",
    "invalid": "That invite link isn't valid here. Make sure you opened the latest link from your manager.",
}


@dataclass(frozen=True)
class StartResult:
    outcome: str  # a RedeemResult outcome, or "member" / "stranger" for a plain /start
    workspace_name: str
    member_payload: Optional[dict] = None  # set when an event should be emitted
    member: Optional[Member] = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ws: uuid.UUID = context.bot_data["workspace_id"]
    user = update.effective_user
    tg = TelegramIdentity(user.id, user.username, user.first_name, user.last_name, user.language_code)
    token = context.args[0] if context.args else None
    result = await asyncio.to_thread(_start_sync, ws, tg, update.effective_chat.id, token)

    if result.member_payload is not None:
        event = "member.updated" if result.outcome == "linked" else "member.joined"
        await emit_workspace_event(ws, event, result.member, source="telegram", member=result.member_payload)
    await update.effective_message.reply_html(reply_text(result))


def reply_text(result: StartResult) -> str:
    name = f"<b>{html.escape(result.workspace_name)}</b>"
    if result.outcome == "joined":
        return f"Welcome to {name} on Nest! 🎉\n\nYour tasks will arrive here, with reminders before they're due. Send /mytasks anytime."
    if result.outcome == "reactivated":
        return f"Welcome back to {name}! Send /mytasks to see your open tasks."
    if result.outcome == "linked":
        return f"Your Telegram is now linked to {name}. You'll get task updates here."
    if result.outcome in ("already_member", "member"):
        return f"You're already in {name}. Send /mytasks to see your open tasks."
    if result.outcome in FAILURES:
        return FAILURES[result.outcome]
    return (
        f"This is {name}'s Nest bot. Nest sends you the tasks your manager assigns.\n\n"
        "To join, open the invite link your manager shared with you."
    )


def _start_sync(ws: uuid.UUID, tg: TelegramIdentity, chat_id: int, token: Optional[str]) -> StartResult:
    with db.SessionLocal() as session:
        if token:
            redeemed: Optional[RedeemResult] = redeem_invite(session, ws, token, tg, chat_id)
        else:
            redeemed = match_pending_by_username(session, ws, tg, chat_id)
        workspace = session.get(Workspace, ws)

        if redeemed is None:  # plain /start, nothing to match
            return StartResult("member" if _is_member(session, ws, tg.id) else "stranger", workspace.name)

        if redeemed.outcome in FAILURES and _is_member(session, ws, tg.id):
            # e.g. re-opening your own single-use link after joining
            return StartResult("already_member", workspace.name)
        if redeemed.outcome not in ("joined", "reactivated", "linked"):
            return StartResult(redeemed.outcome, redeemed.workspace_name or workspace.name)

        member = redeemed.member
        if redeemed.outcome in ("joined", "reactivated"):
            _notify_managers(session, member)
            session.commit()
        payload = MemberSchema.model_validate(member_out(session, member)).model_dump(mode="json", by_alias=True)
        session.expunge(member)
        return StartResult(redeemed.outcome, workspace.name, payload, member)


def _is_member(session, ws: uuid.UUID, telegram_user_id: int) -> bool:
    return (
        session.scalar(
            select(Member.id).where(
                Member.workspace_id == ws,
                Member.telegram_user_id == telegram_user_id,
                Member.status.in_([MemberStatus.ACTIVE, MemberStatus.UNREACHABLE]),
            )
        )
        is not None
    )


def _notify_managers(session, member: Member) -> None:
    """DM every Telegram-linked manager that someone joined."""
    session.refresh(member)
    name = html.escape(display_name(member))
    workspace = session.get(Workspace, member.workspace_id)
    managers = session.scalars(
        select(Member).where(
            Member.workspace_id == member.workspace_id,
            Member.role == MemberRole.MANAGER,
            Member.status == MemberStatus.ACTIVE,
            Member.private_chat_id.is_not(None),
            Member.id != member.id,
        )
    )
    text = f"👋 {name} joined <b>{html.escape(workspace.name)}</b> on Nest. You can assign them tasks now."
    for manager in managers:
        enqueue(
            session,
            workspace_id=member.workspace_id,
            member_id=manager.id,
            type=NotificationType.INVITE,
            # One per redeem: redeem_invite itself makes a repeated /start a no-op.
            idempotency_key=f"joined:{member.id}:{manager.id}:{uuid.uuid4()}",
            payload=message_payload(text),
        )
