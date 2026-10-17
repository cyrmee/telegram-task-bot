"""Socket.IO server, namespace /v1 (docs/api-v1.md §6).

Connect requires the same JWT as REST. The server, never the client,
decides which `ws:{workspaceId}` rooms a socket joins.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import socketio

from app.auth import InvalidToken, verify_token
from app.config import get_settings
from app.db import SessionLocal
from app.models import Member
from app.services.workspaces import manager_workspace_ids

logger = logging.getLogger(__name__)

NAMESPACE = "/v1"

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=get_settings().cors_origins)


def workspace_room(workspace_id: uuid.UUID | str) -> str:
    return f"ws:{workspace_id}"


def user_room(user_id: str) -> str:
    return f"user:{user_id}"


def _workspaces_for(user_id: str) -> list[uuid.UUID]:
    with SessionLocal() as db:
        return manager_workspace_ids(db, user_id)


class V1Namespace(socketio.AsyncNamespace):
    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None):
        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        if not token:
            raise socketio.exceptions.ConnectionRefusedError("unauthenticated")
        try:
            user = await asyncio.to_thread(verify_token, token)
        except InvalidToken:
            raise socketio.exceptions.ConnectionRefusedError("unauthenticated") from None
        workspace_ids = await asyncio.to_thread(_workspaces_for, user.id)
        await self.save_session(sid, {"user_id": user.id})
        await self.enter_room(sid, user_room(user.id))
        for ws_id in workspace_ids:
            await self.enter_room(sid, workspace_room(ws_id))
        logger.info("socket connected", extra={"sid": sid, "workspaces": len(workspace_ids)})


sio.register_namespace(V1Namespace(NAMESPACE))


def _sids_in(room: str) -> list[str]:
    return [sid for sid, _ in sio.manager.get_participants(NAMESPACE, room)]


async def join_user_to_workspace(user_id: str, workspace_id: uuid.UUID) -> None:
    """After a workspace is created or a user is made manager."""
    for sid in _sids_in(user_room(user_id)):
        await sio.enter_room(sid, workspace_room(workspace_id), namespace=NAMESPACE)


async def remove_user_from_workspace(user_id: str, workspace_id: uuid.UUID) -> None:
    """After a manager is removed or demoted."""
    for sid in _sids_in(user_room(user_id)):
        await sio.leave_room(sid, workspace_room(workspace_id), namespace=NAMESPACE)


async def emit_workspace_event(
    workspace_id: uuid.UUID,
    event: str,
    actor: Optional[Member] = None,
    source: str = "dashboard",
    **payload: Any,
) -> None:
    """Emit an envelope-wrapped event to everyone watching the workspace.
    Payload values must already be JSON-ready (camelCase dicts)."""
    message = {
        "event": event,
        "workspaceId": str(workspace_id),
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "actor": {"source": source, "memberId": str(actor.id) if actor else None},
        **payload,
    }
    try:
        await sio.emit(event, message, room=workspace_room(workspace_id), namespace=NAMESPACE)
    except Exception:  # realtime is best-effort; never fail the request over it
        logger.exception("Failed to emit %s", event)
