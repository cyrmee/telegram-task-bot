"""POST /telegram/{botId}: the webhook every workspace bot posts to.

The secret header is checked before the body is read. Unknown bots and wrong
secrets both get 403, so the endpoint doesn't reveal which bot ids are connected.
"""
import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Header, Request, Response, status

from .errors import BadSecret, UnknownBot

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


@router.post("/telegram/{bot_id}")
async def telegram_webhook(
    bot_id: int,
    request: Request,
    secret: Annotated[Optional[str], Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
):
    manager = getattr(request.app.state, "bot_manager", None)
    if manager is None:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not manager.authenticate(bot_id, secret):
        logger.warning("Rejected webhook call for bot %s", bot_id)
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    try:
        data = json.loads(await request.body())
    except ValueError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    try:
        await manager.process_update(bot_id, secret, data)
    except (UnknownBot, BadSecret):  # the bot was disconnected in between
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    except Exception:
        # Telegram retries non-2xx responses; a handler bug shouldn't cause a retry storm.
        logger.exception("Processing update for bot %s failed", bot_id)
    return Response(status_code=status.HTTP_200_OK)
