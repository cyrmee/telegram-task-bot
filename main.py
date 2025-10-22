import os
import logging
import uvicorn
from fastapi import FastAPI
from api import app as fastapi_app
from bot import TaskBot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def run_polling():
    """Run the bot using polling (traditional method)"""
    logger.info("Starting bot in polling mode...")
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        logger.error("Please create a .env file with your bot token.")
        return

    bot = TaskBot(token)
    bot.run()


def run_webhook():
    """Run the bot using webhooks (requires public URL)"""
    logger.info("Starting bot in webhook mode...")
    logger.info("Make sure to set your webhook URL with Telegram API:")
    logger.info(
        "curl -X POST https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_PUBLIC_URL>/webhook/<BOT_ID>"
    )

    # Run FastAPI with Uvicorn
    uvicorn.run(
        "api:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )


def run_combined():
    """Run both FastAPI API and bot in polling mode"""
    logger.info("Starting combined mode: API + Polling bot...")

    # Import here to avoid circular imports
    from multiprocessing import Process
    import time

    # Function to run polling bot
    def run_bot():
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            bot = TaskBot(token)
            bot.run()
        else:
            logger.error("TELEGRAM_BOT_TOKEN not set for polling mode")

    # Start bot in separate process
    bot_process = Process(target=run_bot)
    bot_process.start()

    # Give bot time to start
    time.sleep(2)

    # Run FastAPI
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=False,  # Disable reload in combined mode
    )

    # Wait for bot process
    bot_process.join()


if __name__ == "__main__":
    mode = os.getenv("RUN_MODE", "webhook").lower()

    if mode == "webhook":
        run_webhook()
    elif mode == "combined":
        run_combined()
    else:
        run_polling()
