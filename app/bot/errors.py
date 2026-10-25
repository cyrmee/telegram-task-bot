"""Errors raised by BotManager; importable without loading the bot runtime."""


class BotError(Exception):
    pass


class InvalidBotToken(BotError):
    """Telegram rejected the token (getMe 401)."""


class BotInUse(BotError):
    """The bot is live in another workspace."""


class TelegramUnavailable(BotError):
    """Telegram didn't answer (network error, timeout, 5xx)."""


class UnknownBot(BotError):
    pass


class BadSecret(BotError):
    pass
