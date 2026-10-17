"""The message a notification renders to, and the send seam BotManager implements."""
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class Button:
    """Inline keyboard button. Exactly one of callback_data / url is set."""

    text: str
    callback_data: Optional[str] = None
    url: Optional[str] = None

    def __post_init__(self):
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("Button needs exactly one of callback_data or url")


@dataclass(frozen=True)
class OutgoingMessage:
    workspace_id: uuid.UUID  # selects the workspace's bot
    chat_id: int
    text: str
    buttons: tuple[tuple[Button, ...], ...] = field(default_factory=tuple)  # rows of buttons
    parse_mode: Optional[str] = "HTML"


# Sends one message through the workspace's bot and returns Telegram's message_id.
# Raises an app.notifications.errors.SendError subclass on failure.
SendFn = Callable[[OutgoingMessage], Awaitable[int]]


def message_payload(
    text: str, buttons: list[list[Button]] | None = None, parse_mode: Optional[str] = "HTML"
) -> dict[str, Any]:
    """Build the notifications.payload for a DM. Store the rendered text, so the
    message that gets delivered is the one the change described."""
    return {
        "text": text,
        "parse_mode": parse_mode,
        "buttons": [
            [{k: v for k, v in vars(b).items() if v is not None} for b in row] for row in (buttons or [])
        ],
    }


def message_from_payload(workspace_id: uuid.UUID, chat_id: int, payload: dict[str, Any]) -> OutgoingMessage:
    return OutgoingMessage(
        workspace_id=workspace_id,
        chat_id=chat_id,
        text=payload["text"],
        buttons=tuple(tuple(Button(**b) for b in row) for row in payload.get("buttons", [])),
        parse_mode=payload.get("parse_mode", "HTML"),
    )
