"""D3: the periodic getWebhookInfo check alerts on backlog, URL drift and delivery errors, once each."""
import asyncio
import logging
import time

from app import db
from app.bot.manager import BotManager
from app.models import Workspace

from .fake_telegram import FakeTelegram
from .helpers import TOKEN_A


def test_webhook_check_alerts_once_per_new_problem(runtime, caplog):
    tg = FakeTelegram()
    bot = tg.add_bot(TOKEN_A, 111111, "alpha_nest_bot")
    with db.SessionLocal() as s, s.begin():
        ws = Workspace(name="Monitored", owner_user_id="alice")
        s.add(ws)
        s.flush()
        ws_id = ws.id
    alerts = lambda: [r for r in caplog.records if getattr(r, "alert", None) == "telegram_webhook_unhealthy"]  # noqa: E731

    async def scenario():
        manager = BotManager(public_base_url="https://nest.test", request_factory=tg.request)
        await manager.connect(ws_id, TOKEN_A)
        logging.getLogger("nest.alerts").disabled = False
        with caplog.at_level(logging.ERROR, logger="nest.alerts"):
            (entry,) = await manager.check_webhooks()
            assert entry["url_ok"] is True and alerts() == []

            bot.pending_update_count = 120
            bot.last_error_date, bot.last_error_message = int(time.time()), "Connection refused"
            await manager.check_webhooks()
            assert len(alerts()) == 1
            assert "120 updates pending" in alerts()[0].getMessage() and "Connection refused" in alerts()[0].getMessage()
            assert alerts()[0].workspace_id == str(ws_id)

            bot.pending_update_count = 0
            await manager.check_webhooks()  # same old error: no new alert
            assert len(alerts()) == 1

            bot.webhook_url = "https://someone-else.example/hook"  # another deployment took the webhook
            await manager.check_webhooks()
            assert "doesn't point here" in alerts()[-1].getMessage()
        assert manager.health()["live_bots"] == 1 and manager.health()["last_webhook_check"] is not None
        await manager.stop()

    asyncio.run(scenario())
