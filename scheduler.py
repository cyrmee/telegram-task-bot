from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta, timezone
from telegram import Bot
import logging

from constants import (
    DATE_FORMAT,
    SCHEDULER_INTERVAL_MINUTES,
    TIME_1_HOUR,
    TIME_30_MINUTES,
    REMINDER_MESSAGE,
)

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, bot: Bot, database):
        self.bot = bot
        self.database = database
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.add_job(
            self.check_reminders,
            trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL_MINUTES),
            id="reminder_checker",
            name="Check for task reminders",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Task scheduler started")

    async def check_reminders(self):
        try:
            now = datetime.now(timezone.utc)

            pending_reminders = self.database.get_pending_reminders()

            for reminder in pending_reminders:
                task = reminder["task"]
                if task["due_date"].tzinfo is None:
                    due_date_utc = task["due_date"].replace(tzinfo=timezone.utc)
                else:
                    due_date_utc = task["due_date"]

                reminder_time = due_date_utc - timedelta(
                    minutes=reminder["minutes_before"]
                )

                if reminder_time <= now < reminder_time + timedelta(minutes=1):
                    await self.send_task_reminder(task, reminder["minutes_before"])
                    self.database.mark_reminder_sent(reminder["id"])
                    logger.info(
                        f"Sent reminder for task {task['id']}: {task['task_name']} ({reminder['minutes_before']} minutes before)"
                    )

        except Exception as e:
            logger.error(f"Error checking reminders: {e}", exc_info=True)

    async def send_task_reminder(self, task, reminder_minutes):
        due_date_str = task["due_date"].strftime(DATE_FORMAT)

        opted_in_users = [
            user for user in task["assigned_users"] if user["receive_reminders"]
        ]

        if not opted_in_users:
            logger.info(f"No opted-in users for task {task['id']}")
            return

        user_mentions = []
        for user in opted_in_users:
            if user["username"]:
                user_mentions.append(f"@{user['username']}")
            else:
                # Try to get user info from Telegram if we don't have their username
                display_name = user.get("first_name") or "User"
                if user.get("last_name"):
                    display_name += f" {user['last_name']}"
                user_mentions.append(display_name)

        if reminder_minutes == 60:
            time_str = TIME_1_HOUR
        elif reminder_minutes == 30:
            time_str = TIME_30_MINUTES
        else:
            time_str = f"{reminder_minutes} minutes"

        message = REMINDER_MESSAGE.format(
            task_name=task["task_name"],
            task_code=task["task_code"],
            due_date_str=due_date_str,
            user_mentions=", ".join(user_mentions),
            time_str=time_str,
        )

        try:
            await self.bot.send_message(
                chat_id=task["chat_id"], text=message, parse_mode="HTML"
            )
            logger.info(
                f"Reminder sent to chat {task['chat_id']} for task: {task['task_name']} ({time_str})"
            )
        except Exception as e:
            logger.error(
                f"Failed to send reminder for task {task['id']}: {e}", exc_info=True
            )

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Task scheduler stopped")
