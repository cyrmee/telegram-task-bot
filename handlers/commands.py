"""
Command handlers for the Telegram Task Bot.
Handles user commands like /start, /receive_reminders, /add_task, and /my_tasks.
"""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatType
from datetime import datetime, timezone
import re
import logging

from constants import DATE_FORMAT

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, database):
    """
    Handle /start command - Register user in the database.

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
    """
    user = update.effective_user

    # Register user in database
    database.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    welcome_message = (
        f"👋 Hello {user.first_name}!\n\n"
        f"Welcome to the Task Management Bot!\n\n"
        f"<b>Available Commands:</b>\n"
        f"• /start - Register/update your profile\n"
        f"• /add_task - Add a new task (admins only, in groups)\n"
        f"• /my_tasks - View your assigned tasks\n"
        f"• /edit_task_reminders - Customize reminder settings for your tasks\n\n"
        f"<b>Note:</b> You will receive reminders by default. "
        f"Use /edit_task_reminders to customize or disable them!"
    )

    await update.message.reply_text(welcome_message, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) registered via /start")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE, database):
    """
    Handle /help command - Show available commands and usage information.

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
    """
    user = update.effective_user

    help_message = (
        "🤖 <b>Task Management Bot Help</b>\n\n"
        "<b>Available Commands:</b>\n"
        "• /start - Register/update your profile\n"
        "• /help - Show this help message\n"
        "• /add_task - Add a new task (admins only, in groups)\n"
        "• /my_tasks - View your assigned tasks\n"
        "• /edit_task_reminders - Customize reminder settings for your tasks\n\n"
        "<b>Task Creation Examples:</b>\n"
        "• /add_task Prepare quarterly report for @john, due tomorrow at 2 PM\n"
        "• /add_task @mike needs to finish the website design by next Friday\n"
        "• /add_task Code review with @sarah and @tom, remind 1 hour and 15 minutes before\n\n"
        "<b>Reminder Customization:</b>\n"
        "• /edit_task_reminders 1 60,30,15 (remind at 1h, 30m, 15m before)\n"
        "• /edit_task_reminders 1 off (disable reminders)\n\n"
        "<b>Note:</b> You will receive reminders by default. Use /edit_task_reminders to customize or disable them!"
    )

    await update.message.reply_text(help_message, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) requested help")


async def add_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database, ai_parser
):
    """
    Handle /add_task command - Add a new task using natural language (admins only, groups only).

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
        ai_parser (TaskParser): AI parser for natural language processing
    """
    user = update.effective_user
    chat = update.effective_chat

    # Check if command is used in a group
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await update.message.reply_text("⚠️ This command can only be used in groups.")
        return

    # Check if user is an admin
    member = await chat.get_member(user.id)
    if member.status not in ["creator", "administrator"]:
        await update.message.reply_text("⚠️ Only group administrators can add tasks.")
        return

    # Check if there's any text after the command
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Please describe the task in natural language!</b>\n\n"
            "<b>Examples:</b>\n"
            "• /add_task Prepare the quarterly report for @john and @jane, due tomorrow at 2 PM\n"
            "• /add_task @mike needs to finish the website design by next Friday\n"
            "• /add_task Code review for the new feature with @sarah and @tom, deadline is 2025-10-25 15:00\n\n"
            "<b>Note:</b> Mention users with @username and specify dates naturally (tomorrow, next week, etc.)",
            parse_mode="HTML",
        )
        return

    try:
        # Join all arguments to get the natural language description
        task_description = " ".join(context.args)

        # Get all users who have registered with the bot (for the AI to match against)
        session = database.get_session()
        try:
            from database import User

            available_users = [
                {
                    "id": u.id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                }
                for u in session.query(User).all()
            ]
        finally:
            session.close()

        # Use AI to parse the natural language description
        parsed_data = ai_parser.parse_task_description(
            task_description, available_users
        )

        task_name = parsed_data["task_name"]
        usernames = parsed_data["usernames"]
        due_date_str = parsed_data["due_date"]
        confidence = parsed_data["confidence"]
        reminder_minutes_list = parsed_data.get("reminder_minutes_list", [30])

        # Parse the due date
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )

        # Check if date is in the future
        if due_date <= datetime.now(timezone.utc):
            await update.message.reply_text(
                "⚠️ Due date must be in the future (UTC time).\n"
                f"Parsed date: {due_date_str}"
            )
            return
        # Validate that we have at least one user
        if not usernames:
            await update.message.reply_text(
                "⚠️ No users were mentioned in the task description.\n"
                "Please mention users with @username (e.g., @john, @jane)"
            )
            return

        # Get user IDs for the mentioned usernames
        assigned_user_ids = []
        session = database.get_session()
        try:
            from database import User

            for username in usernames:
                user_obj = session.query(User).filter_by(username=username).first()
                if user_obj:
                    assigned_user_ids.append(user_obj.id)
                else:
                    logger.warning(f"User @{username} not found in database")
        finally:
            session.close()

        if not assigned_user_ids:
            await update.message.reply_text(
                "⚠️ Could not find the mentioned users in the database.\n"
                "Users must have started the bot first using /start.\n"
                f"Mentioned users: {', '.join([f'@{u}' for u in usernames])}"
            )
            return

        # Create the task
        task = database.add_task(
            task_name=task_name,
            chat_id=chat.id,
            due_date=due_date,
            assigned_user_ids=assigned_user_ids,
            reminder_minutes_list=reminder_minutes_list,
        )

        # Format response
        user_list = ", ".join([f"@{u}" for u in usernames])
        due_date_display = due_date.strftime(DATE_FORMAT)

        # Add confidence indicator
        if confidence > 0.8:
            confidence_icon = "🎯"
        elif confidence > 0.6:
            confidence_icon = "⚠️"
        else:
            confidence_icon = "❓"
        confidence_text = f"{confidence_icon} AI confidence: {confidence:.1%}"

        # Format reminder info
        if reminder_minutes_list:
            if len(reminder_minutes_list) == 1:
                minutes = reminder_minutes_list[0]
                if minutes == 60:
                    reminder_text = (
                        "🔔 Reminder will be sent 1 hour before the deadline."
                    )
                elif minutes == 30:
                    reminder_text = (
                        "🔔 Reminder will be sent 30 minutes before the deadline."
                    )
                else:
                    reminder_text = f"🔔 Reminder will be sent {minutes} minutes before the deadline."
            else:
                reminder_parts = []
                for minutes in sorted(reminder_minutes_list):
                    if minutes == 60:
                        reminder_parts.append("1 hour")
                    elif minutes == 30:
                        reminder_parts.append("30 minutes")
                    else:
                        reminder_parts.append(f"{minutes} minutes")
                reminder_text = f"🔔 Reminders will be sent {', '.join(reminder_parts)} before the deadline."
        else:
            reminder_text = "🔕 No reminders will be sent for this task."

        response = (
            f"✅ <b>Task Created with AI!</b>\n\n"
            f"📋 <b>Task:</b> {task_name}\n"
            f"👥 <b>Assigned to:</b> {user_list}\n"
            f"⏰ <b>Due:</b> {due_date_display}\n"
            f"{reminder_text}\n"
            f"{confidence_text}"
        )

        await update.message.reply_text(response, parse_mode="HTML")
        logger.info(
            f"AI-parsed task created: {task_name} (ID: {task.id}) by user {user.id} (confidence: {confidence:.2f})"
        )

    except ValueError as e:
        await update.message.reply_text(
            f"❌ <b>AI Parsing Error:</b> {str(e)}\n\n"
            "<b>Try rephrasing your task description. Examples:</b>\n"
            "• /add_task Prepare the quarterly report for @john and @jane, due tomorrow at 2 PM\n"
            "• /add_task @mike needs to finish the website design by next Friday\n"
            "• /add_task Code review for the new feature with @sarah and @tom, deadline is 2025-10-25 15:00",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error creating AI-parsed task: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while processing your task with AI. Please try again with a simpler description."
        )


async def my_tasks_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """
    Handle /my_tasks command - Show all tasks assigned to the user.

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
    """
    user = update.effective_user

    # Get user's tasks
    tasks = database.get_user_tasks(user.id)

    if not tasks:
        await update.message.reply_text("📭 You have no active tasks assigned to you.")
        return

    # Format task list
    response = "📋 <b>Your Active Tasks:</b>\n\n"

    for i, task in enumerate(tasks, 1):
        due_date_str = task.due_date.strftime(DATE_FORMAT)

        # Calculate time remaining
        # Ensure due_date is treated as UTC (it might be naive from database)
        if task.due_date.tzinfo is None:
            due_date_utc = task.due_date.replace(tzinfo=timezone.utc)
        else:
            due_date_utc = task.due_date

        time_remaining = due_date_utc - datetime.now(timezone.utc)
        days = time_remaining.days
        hours = time_remaining.seconds // 3600

        if days > 0:
            time_str = f"{days} day(s) {hours} hour(s)"
        elif hours > 0:
            time_str = f"{hours} hour(s)"
        else:
            minutes = time_remaining.seconds // 60
            time_str = f"{minutes} minute(s)"

        response += (
            f"<b>{i}.</b> {task.task_name}\n"
            f"   ⏰ Due: {due_date_str}\n"
            f"   ⏳ Time left: {time_str}\n\n"
        )

    await update.message.reply_text(response, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) viewed their tasks")


async def edit_task_reminders_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """
    Handle /edit_task_reminders command - Allow users to modify reminder settings for their tasks.

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
    """
    user = update.effective_user

    # Get user's tasks
    tasks = database.get_user_tasks(user.id)

    if not tasks:
        await update.message.reply_text("📭 You have no active tasks assigned to you.")
        return

    # Check if task ID is provided
    if not context.args:
        # Show list of user's tasks with their current reminder settings
        response = "📋 <b>Your Tasks & Reminder Settings:</b>\n\n"
        response += "Use: /edit_task_reminders <task_number> <reminder_times>\n\n"
        response += "Examples:\n"
        response += (
            "• /edit_task_reminders 1 60,30,15 (remind at 1h, 30m, 15m before)\n"
        )
        response += "• /edit_task_reminders 1 120 (remind 2 hours before)\n"
        response += "• /edit_task_reminders 1 off (disable reminders)\n\n"

        for i, task in enumerate(tasks, 1):
            due_date_str = task.due_date.strftime(DATE_FORMAT)

            # Get reminder times for this task
            reminder_times = [r.minutes_before for r in task.reminders]
            if reminder_times:
                reminder_parts = []
                for minutes in sorted(reminder_times):
                    if minutes == 60:
                        reminder_parts.append("1h")
                    elif minutes == 30:
                        reminder_parts.append("30m")
                    else:
                        reminder_parts.append(f"{minutes}m")
                reminder_str = ", ".join(reminder_parts)
            else:
                reminder_str = "disabled"

            response += (
                f"<b>{i}.</b> {task.task_name}\n"
                f"   ⏰ Due: {due_date_str}\n"
                f"   🔔 Reminders: {reminder_str}\n\n"
            )

        await update.message.reply_text(response, parse_mode="HTML")
        return

    try:
        # Parse arguments
        task_number = int(context.args[0]) - 1  # Convert to 0-based index

        if task_number < 0 or task_number >= len(tasks):
            await update.message.reply_text("❌ Invalid task number.")
            return

        task = tasks[task_number]

        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Please specify the new reminder setting.\n\n"
                "Examples:\n"
                "• /edit_task_reminders 1 60,30,15 (multiple reminders)\n"
                "• /edit_task_reminders 1 120 (single reminder)\n"
                "• /edit_task_reminders 1 off (disable reminders)"
            )
            return

        reminder_setting = context.args[1].lower()

        if reminder_setting == "off":
            # Disable reminders
            success = database.update_task_reminders(task.id, reminder_minutes_list=[])
            if success:
                await update.message.reply_text(
                    f"✅ <b>Reminders disabled for task:</b> {task.task_name}\n\n"
                    f"🔕 No reminders will be sent for this task.",
                    parse_mode="HTML",
                )
                logger.info(f"User {user.id} disabled reminders for task {task.id}")
            else:
                await update.message.reply_text("❌ Error updating task reminders.")
        else:
            # Parse reminder times (comma-separated)
            try:
                reminder_times_str = reminder_setting.split(",")
                reminder_minutes_list = []

                for time_str in reminder_times_str:
                    time_str = time_str.strip()
                    if not time_str:
                        continue
                    minutes = int(time_str)
                    if minutes <= 0:
                        await update.message.reply_text(
                            "❌ Reminder times must be positive numbers."
                        )
                        return
                    reminder_minutes_list.append(minutes)

                if not reminder_minutes_list:
                    await update.message.reply_text(
                        "❌ Please specify at least one reminder time."
                    )
                    return

                success = database.update_task_reminders(
                    task.id, reminder_minutes_list=reminder_minutes_list
                )
                if success:
                    if len(reminder_minutes_list) == 1:
                        minutes = reminder_minutes_list[0]
                        if minutes == 60:
                            time_str = "1 hour"
                        elif minutes == 30:
                            time_str = "30 minutes"
                        else:
                            time_str = f"{minutes} minutes"
                        message = f"✅ <b>Reminder updated for task:</b> {task.task_name}\n\n🔔 Reminder will be sent {time_str} before the deadline."
                    else:
                        reminder_parts = []
                        for minutes in sorted(reminder_minutes_list):
                            if minutes == 60:
                                reminder_parts.append("1 hour")
                            elif minutes == 30:
                                reminder_parts.append("30 minutes")
                            else:
                                reminder_parts.append(f"{minutes} minutes")
                        message = f"✅ <b>Reminders updated for task:</b> {task.task_name}\n\n🔔 Reminders will be sent {', '.join(reminder_parts)} before the deadline."

                    await update.message.reply_text(message, parse_mode="HTML")
                    logger.info(
                        f"User {user.id} updated reminders for task {task.id} to {reminder_minutes_list}"
                    )
                else:
                    await update.message.reply_text("❌ Error updating task reminders.")
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid reminder times. Please use comma-separated numbers (minutes).\n\n"
                    "Examples:\n"
                    "• /edit_task_reminders 1 60,30,15\n"
                    "• /edit_task_reminders 1 120\n"
                    "• /edit_task_reminders 1 off"
                )

    except ValueError:
        await update.message.reply_text("❌ Invalid task number. Please use a number.")
    except Exception as e:
        logger.error(f"Error editing task reminders: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while updating task reminders."
        )
