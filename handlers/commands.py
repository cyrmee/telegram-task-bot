"""
Command handlers for the Telegram Task Bot.
Handles user commands like /start, /receive_reminders, /add_task, and /my_tasks.
"""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatType
from datetime import datetime
import re
import logging

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
        f"• /receive_reminders - Opt in to receive task reminders\n"
        f"• /add_task - Add a new task (admins only, in groups)\n"
        f"• /my_tasks - View your assigned tasks\n\n"
        f"<b>Note:</b> By default, you won't receive reminders. "
        f"Use /receive_reminders to opt in!"
    )

    await update.message.reply_text(welcome_message, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) registered via /start")


async def receive_reminders_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """
    Handle /receive_reminders command - Enable reminders for the user.
    This command must be sent in a private chat (opt-in requirement).

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
    """
    user = update.effective_user
    chat = update.effective_chat

    # Ensure this is a private chat (DM)
    if chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            "⚠️ Please send this command in a private message to me, not in a group."
        )
        return

    # Enable reminders for the user
    success = database.enable_reminders(user.id)

    if success:
        await update.message.reply_text(
            "✅ You have opted in to receive task reminders!\n\n"
            "You will now receive notifications 30 minutes before task deadlines."
        )
        logger.info(f"User {user.id} ({user.username}) enabled reminders")
    else:
        await update.message.reply_text(
            "❌ Error: Please use /start first to register."
        )


async def add_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """
    Handle /add_task command - Add a new task (admins only, groups only).
    Format: /add_task "task name" @user1 @user2 YYYY-MM-DD HH:MM

    Args:
        update (Update): Telegram update object
        context (ContextTypes.DEFAULT_TYPE): Telegram context
        database (Database): Database instance
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

    # Parse the command arguments
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            '/add_task "task name" @user1 @user2 YYYY-MM-DD HH:MM\n\n'
            "<b>Example:</b>\n"
            '/add_task "Prepare presentation" @john @jane 2025-10-20 14:30',
            parse_mode="HTML",
        )
        return

    try:
        # Join all arguments and parse
        full_text = " ".join(context.args)

        # Extract task name (in quotes)
        task_match = re.search(r'"([^"]+)"', full_text)
        if not task_match:
            raise ValueError("Task name must be in quotes")

        task_name = task_match.group(1)
        remaining_text = full_text[task_match.end() :].strip()

        # Extract mentions (@username)
        mentions = re.findall(r"@(\w+)", remaining_text)
        if not mentions:
            raise ValueError("At least one user must be mentioned")

        # Remove mentions from remaining text
        date_time_text = re.sub(r"@\w+", "", remaining_text).strip()

        # Parse date and time (YYYY-MM-DD HH:MM)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", date_time_text)
        if not date_match:
            raise ValueError("Invalid date/time format. Use YYYY-MM-DD HH:MM")

        due_date = datetime.strptime(
            f"{date_match.group(1)} {date_match.group(2)}", "%Y-%m-%d %H:%M"
        )

        # Check if date is in the future
        if due_date <= datetime.utcnow():
            await update.message.reply_text(
                "⚠️ Due date must be in the future (UTC time)."
            )
            return

        # Get mentioned users from the database
        assigned_user_ids = []
        mentioned_entities = update.message.parse_entities(["mention", "text_mention"])

        for entity, text in mentioned_entities.items():
            if entity.type == "text_mention":
                # Direct user mention
                assigned_user_ids.append(entity.user.id)
            elif entity.type == "mention":
                # Username mention - need to find user ID
                username = text.lstrip("@")
                # Try to get user from database
                session = database.get_session()
                try:
                    from database import User

                    user_obj = session.query(User).filter_by(username=username).first()
                    if user_obj:
                        assigned_user_ids.append(user_obj.id)
                finally:
                    session.close()

        if not assigned_user_ids:
            await update.message.reply_text(
                "⚠️ Could not find mentioned users in database.\n"
                "Users must have started the bot first using /start."
            )
            return

        # Create the task
        task = database.add_task(
            task_name=task_name,
            chat_id=chat.id,
            due_date=due_date,
            assigned_user_ids=assigned_user_ids,
        )

        # Format response
        user_list = ", ".join([f"@{m}" for m in mentions])
        due_date_str = due_date.strftime("%Y-%m-%d %H:%M UTC")

        response = (
            f"✅ <b>Task Created!</b>\n\n"
            f"📋 <b>Task:</b> {task_name}\n"
            f"👥 <b>Assigned to:</b> {user_list}\n"
            f"⏰ <b>Due:</b> {due_date_str}\n\n"
            f"🔔 Reminders will be sent 30 minutes before the deadline."
        )

        await update.message.reply_text(response, parse_mode="HTML")
        logger.info(f"Task created: {task_name} (ID: {task.id}) by user {user.id}")

    except ValueError as e:
        await update.message.reply_text(
            f"❌ <b>Error:</b> {str(e)}\n\n"
            "<b>Usage:</b>\n"
            '/add_task "task name" @user1 @user2 YYYY-MM-DD HH:MM\n\n'
            "<b>Example:</b>\n"
            '/add_task "Prepare presentation" @john @jane 2025-10-20 14:30',
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while creating the task. Please try again."
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
        due_date_str = task.due_date.strftime("%Y-%m-%d %H:%M UTC")

        # Calculate time remaining
        time_remaining = task.due_date - datetime.utcnow()
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
