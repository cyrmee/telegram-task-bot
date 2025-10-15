"""
Scheduler module for managing task reminders using APScheduler.
Checks for tasks due in 30 minutes and sends reminders to opted-in users.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from telegram import Bot
import logging

logger = logging.getLogger(__name__)


class TaskScheduler:
    """
    Manages scheduled task reminders using APScheduler.
    """

    def __init__(self, bot: Bot, database):
        """
        Initialize the scheduler with bot instance and database.

        Args:
            bot (Bot): Telegram Bot instance
            database (Database): Database instance
        """
        self.bot = bot
        self.database = database
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """
        Start the scheduler to check for reminders every minute.
        """
        # Check for reminders every minute
        self.scheduler.add_job(
            self.check_reminders,
            trigger=IntervalTrigger(minutes=1),
            id="reminder_checker",
            name="Check for task reminders",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Task scheduler started")

    async def check_reminders(self):
        """
        Check for tasks that need reminders sent (30 minutes before deadline).
        This function runs periodically to identify tasks approaching their deadline.
        """
        try:
            # Get current time and 30 minutes from now
            now = datetime.utcnow()
            reminder_window_start = now + timedelta(minutes=29)
            reminder_window_end = now + timedelta(minutes=31)

            # Get all pending tasks that haven't had reminders sent
            pending_tasks = self.database.get_pending_reminders()

            for task in pending_tasks:
                # Check if task is due within the 30-minute window
                if reminder_window_start <= task.due_date <= reminder_window_end:
                    await self.send_task_reminder(task)
                    self.database.mark_reminder_sent(task.id)
                    logger.info(f"Sent reminder for task {task.id}: {task.task_name}")

        except Exception as e:
            logger.error(f"Error checking reminders: {e}", exc_info=True)

    async def send_task_reminder(self, task):
        """
        Send reminder message to all assigned users who opted in.

        Args:
            task (Task): Task object to send reminder for
        """
        # Format the due date for display
        due_date_str = task.due_date.strftime("%Y-%m-%d %H:%M UTC")

        # Get users who should receive the reminder
        opted_in_users = [
            user for user in task.assigned_users if user.receive_reminders
        ]

        if not opted_in_users:
            logger.info(f"No opted-in users for task {task.id}")
            return

        # Create list of usernames for the message
        user_mentions = []
        for user in opted_in_users:
            if user.username:
                user_mentions.append(f"@{user.username}")
            else:
                user_mentions.append(user.first_name or f"User {user.id}")

        # Compose reminder message
        message = (
            f"🔔 <b>Task Reminder</b>\n\n"
            f"📋 <b>Task:</b> {task.task_name}\n"
            f"⏰ <b>Due:</b> {due_date_str}\n"
            f"👥 <b>Assigned to:</b> {', '.join(user_mentions)}\n\n"
            f"⚠️ This task is due in approximately 30 minutes!"
        )

        try:
            # Send reminder to the group chat
            await self.bot.send_message(
                chat_id=task.chat_id, text=message, parse_mode="HTML"
            )
            logger.info(
                f"Reminder sent to chat {task.chat_id} for task: {task.task_name}"
            )
        except Exception as e:
            logger.error(
                f"Failed to send reminder for task {task.id}: {e}", exc_info=True
            )

    def shutdown(self):
        """
        Gracefully shutdown the scheduler.
        """
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Task scheduler stopped")
