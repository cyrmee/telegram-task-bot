"""POST /telegram/{botId}: the webhook every workspace bot posts to."""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Header, Request, Response, status

from .manager import BadSecret, UnknownBot

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
    try:
        await manager.process_update(bot_id, secret, await request.json())
    except UnknownBot:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    except BadSecret:
        logger.warning("Rejected webhook call with a bad secret for bot %s", bot_id)
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    except Exception:
        # Telegram retries non-2xx responses; a handler bug shouldn't cause a retry storm.
        logger.exception("Processing update for bot %s failed", bot_id)
    return Response(status_code=status.HTTP_200_OK)
