"""
Scheduler module for managing task reminders using APScheduler.
Checks for tasks due in 30 minutes and sends reminders to opted-in users.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta, timezone
from telegram import Bot
import logging

from constants import DATE_FORMAT

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
        Check for reminders that need to be sent (multiple reminders per task).
        This function runs periodically to identify reminders approaching their send time.
        """
        try:
            # Get current time
            now = datetime.now(timezone.utc)

            # Get all pending reminders that haven't been sent
            pending_reminders = self.database.get_pending_reminders()

            for reminder in pending_reminders:
                # Ensure due_date is treated as UTC (it might be naive from database)
                if reminder.task.due_date.tzinfo is None:
                    due_date_utc = reminder.task.due_date.replace(tzinfo=timezone.utc)
                else:
                    due_date_utc = reminder.task.due_date

                # Calculate reminder time
                reminder_time = due_date_utc - timedelta(
                    minutes=reminder.minutes_before
                )

                # Check if it's time to send the reminder (within a 1-minute window)
                if reminder_time <= now < reminder_time + timedelta(minutes=1):
                    await self.send_task_reminder(
                        reminder.task, reminder.minutes_before
                    )
                    self.database.mark_reminder_sent(reminder.id)
                    logger.info(
                        f"Sent reminder for task {reminder.task.id}: {reminder.task.task_name} ({reminder.minutes_before} minutes before)"
                    )

        except Exception as e:
            logger.error(f"Error checking reminders: {e}", exc_info=True)

    async def send_task_reminder(self, task, reminder_minutes):
        """
        Send reminder message to all assigned users who opted in.

        Args:
            task (Task): Task object to send reminder for
            reminder_minutes (int): Minutes before due date for this reminder
        """
        # Format the due date for display
        due_date_str = task.due_date.strftime(DATE_FORMAT)

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

        # Format reminder time for display
        if reminder_minutes == 60:
            time_str = "1 hour"
        elif reminder_minutes == 30:
            time_str = "30 minutes"
        else:
            time_str = f"{reminder_minutes} minutes"

        # Compose reminder message
        message = (
            f"🔔 <b>Task Reminder</b>\n\n"
            f"📋 <b>Task:</b> {task.task_name}\n"
            f"⏰ <b>Due:</b> {due_date_str}\n"
            f"👥 <b>Assigned to:</b> {', '.join(user_mentions)}\n\n"
            f"⚠️ This task is due in approximately {time_str}!"
        )

        try:
            # Send reminder to the group chat
            await self.bot.send_message(
                chat_id=task.chat_id, text=message, parse_mode="HTML"
            )
            logger.info(
                f"Reminder sent to chat {task.chat_id} for task: {task.task_name} ({time_str})"
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
