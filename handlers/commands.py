from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatType
from datetime import datetime, timezone
import re
import logging
import html

from constants import (
    DATE_FORMAT,
    START_MESSAGE,
    HELP_MESSAGE,
    ADD_TASK_GROUP_ONLY,
    ADD_TASK_ADMIN_ONLY,
    ADD_TASK_NO_DESCRIPTION,
    ADD_TASK_PAST_DATE,
    ADD_TASK_AI_ERROR,
    ADD_TASK_SUCCESS,
    ADD_TASK_UNEXPECTED_ERROR,
    MY_TASKS_NONE,
    EDIT_REMINDERS_USAGE,
    EDIT_REMINDERS_INVALID_TASK,
    EDIT_REMINDERS_NO_SETTING,
    EDIT_REMINDERS_NEGATIVE_TIME,
    EDIT_REMINDERS_NO_TIMES,
    EDIT_REMINDERS_INVALID_TIMES,
    EDIT_REMINDERS_DISABLED,
    EDIT_REMINDERS_UPDATED_SINGLE,
    EDIT_REMINDERS_UPDATED_MULTIPLE,
    EDIT_REMINDERS_ERROR,
    EDIT_REMINDERS_INVALID_NUMBER,
    EDIT_REMINDERS_UPDATE_ERROR,
    TIME_1_HOUR,
    TIME_30_MINUTES,
)

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, database):
    user = update.effective_user

    database.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    welcome_message = START_MESSAGE.format(user_first_name=user.first_name)

    await update.message.reply_text(welcome_message, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) registered via /start")


async def register_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    # /register is just an alias for /start
    await start_command(update, context, database)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_text(HELP_MESSAGE, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) requested help")


async def add_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database, ai_parser
):
    user = update.effective_user
    chat = update.effective_chat

    # Register the user if not already registered
    database.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await update.message.reply_text(ADD_TASK_GROUP_ONLY)
        return

    member = await chat.get_member(user.id)
    if member.status not in ["creator", "administrator"]:
        await update.message.reply_text(ADD_TASK_ADMIN_ONLY)
        return

    if not context.args:
        await update.message.reply_text(ADD_TASK_NO_DESCRIPTION, parse_mode="HTML")
        return

    try:
        task_description = " ".join(context.args)

        session = database.get_session()
        try:
            from models import User

            available_users = [
                {
                    "id": u.telegram_id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                }
                for u in session.query(User).all()
            ]
        finally:
            session.close()

        parsed_data = ai_parser.parse_task_description(
            task_description, available_users
        )

        task_name = parsed_data["task_name"]
        usernames = parsed_data["usernames"]
        due_date_str = parsed_data["due_date"]
        confidence = parsed_data["confidence"]
        reminder_minutes_list = parsed_data.get("reminder_minutes_list", [30])

        due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )

        if due_date <= datetime.now(timezone.utc):
            await update.message.reply_text(
                ADD_TASK_PAST_DATE.format(due_date_str=due_date_str)
            )
            return

        # Collect mentioned user IDs from text mentions and entities
        mentioned_user_ids = set()
        mentioned_usernames_from_entities = set()  # @username from entities

        # Process both text mentions and @username mentions from entities
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "text_mention" and entity.user:
                    # This is a user mentioned by tapping their name (not @username)
                    mentioned_user = entity.user
                    mentioned_user_ids.add(mentioned_user.id)

                    # Register/update this user in database (they don't need to /start first)
                    database.add_user(
                        user_id=mentioned_user.id,
                        username=mentioned_user.username,
                        first_name=mentioned_user.first_name,
                        last_name=mentioned_user.last_name,
                    )
                elif entity.type == "mention":
                    # This is @username mention - extract the username
                    username_text = update.message.text[
                        entity.offset : entity.offset + entity.length
                    ]
                    clean_username = username_text.lstrip("@")
                    mentioned_usernames_from_entities.add(clean_username)

        # Merge AI-parsed usernames with entity-detected @usernames
        all_usernames = set(usernames) | mentioned_usernames_from_entities

        # Process AI-parsed usernames and @mentions
        if not all_usernames and not mentioned_user_ids:
            await update.message.reply_text(
                "❓ I couldn't identify any specific users in your task.\n\n"
                "Please mention users with @username (like @john) or by tapping their name so I know who to assign the task to.\n\n"
                f"Example: /add_task {task_description} @username"
            )
            return

        # Try to get user IDs from group chat members for @username mentions
        unresolved_usernames = []
        for username in all_usernames:
            clean_username = username.lstrip("@")
            user_found = False

            # Try to get chat administrators and members to find the user
            try:
                # Get chat administrators first (most likely to be mentioned)
                admins = await chat.get_administrators()
                for admin in admins:
                    if (
                        admin.user.username
                        and admin.user.username.lower() == clean_username.lower()
                    ):
                        mentioned_user_ids.add(admin.user.id)
                        # Register/update this user
                        database.add_user(
                            user_id=admin.user.id,
                            username=admin.user.username,
                            first_name=admin.user.first_name,
                            last_name=admin.user.last_name,
                        )
                        user_found = True
                        break
            except Exception as e:
                logger.debug(f"Could not fetch chat administrators: {e}")

            if not user_found:
                # Fallback: check our database
                session = database.get_session()
                try:
                    from models import User

                    user_obj = (
                        session.query(User).filter_by(username=clean_username).first()
                    )
                    if user_obj:
                        mentioned_user_ids.add(user_obj.telegram_id)
                        user_found = True
                finally:
                    session.close()

            if not user_found:
                unresolved_usernames.append(username)

        # Resolve any remaining usernames by display name matching
        unregistered_usernames = []
        if unresolved_usernames:
            session = database.get_session()
            try:
                from models import User

                for username in unresolved_usernames:
                    user_obj = None
                    clean_username = username.lstrip("@")

                    # Try to match by display name (first_name + last_name)
                    for u in session.query(User).all():
                        display_name = f"{u.first_name} {u.last_name or ''}".strip()
                        if display_name.lower() == clean_username.lower():
                            user_obj = u
                            break

                    if not user_obj:
                        # Try to match by first name only
                        user_obj = (
                            session.query(User)
                            .filter(User.first_name.ilike(clean_username))
                            .first()
                        )

                    if not user_obj:
                        # Try to match by last name only
                        user_obj = (
                            session.query(User)
                            .filter(User.last_name.ilike(clean_username))
                            .first()
                        )

                    if user_obj:
                        mentioned_user_ids.add(user_obj.telegram_id)
                    else:
                        unregistered_usernames.append(username)
            finally:
                session.close()

        # Convert set to list for database operations
        assigned_user_ids = list(mentioned_user_ids)

        # Notify about unregistered @username mentions
        if unregistered_usernames:
            mentions = " ".join([f"@{username}" for username in unregistered_usernames])
            await update.message.reply_text(
                f"ℹ️ <b>Registration Required:</b> I couldn't find {mentions} in my database.\n\n"
                f"<b>To assign tasks to users:</b>\n"
                f"• Ask them to send me a private message with /start to register\n"
                f"• Or tap their name in the group to mention them directly\n\n"
                f"<b>Why register?</b> It allows me to send them reminders and notifications!\n\n"
                f"I'll create the task with the users I could identify.",
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id,
            )

        if not assigned_user_ids:
            await update.message.reply_text(
                f"⚠️ No users could be identified for this task.\n\n"
                f"<b>To assign tasks:</b>\n"
                f"• Tap user names in the group to mention them\n"
                f"• Or use @username for users who have already used /start\n\n"
                f"Example: Tap on someone's name or use /add_task {task_description.split()[0]} @username",
                parse_mode="HTML",
            )
            return

        task = database.add_task(
            task_name=task_name,
            chat_id=chat.id,
            due_date=due_date,
            assigned_user_ids=assigned_user_ids,
            reminder_minutes_list=reminder_minutes_list,
        )

        if not task or not isinstance(task, dict) or "id" not in task:
            logger.error(f"Invalid task returned from database.add_task: {task}")
            await update.message.reply_text(ADD_TASK_UNEXPECTED_ERROR)
            return

        # Build user list for display using user IDs
        user_display_names = []
        session = database.get_session()
        try:
            from models import User

            for user_id in assigned_user_ids:
                user_obj = session.query(User).filter_by(telegram_id=user_id).first()
                if user_obj:
                    if user_obj.username:
                        user_display_names.append(f"@{user_obj.username}")
                    else:
                        display_name = f"{user_obj.first_name}"
                        if user_obj.last_name:
                            display_name += f" {user_obj.last_name}"
                        user_display_names.append(display_name)
        finally:
            session.close()

        user_list = ", ".join(user_display_names)

        # Escape HTML characters in user_list
        import html

        user_list = html.escape(user_list)

        due_date_display = due_date.strftime(DATE_FORMAT)

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
                        reminder_parts.append(TIME_1_HOUR)
                    elif minutes == 30:
                        reminder_parts.append(TIME_30_MINUTES)
                    else:
                        reminder_parts.append(f"{minutes} minutes")
                reminder_text = f"🔔 Reminders will be sent {', '.join(reminder_parts)} before the deadline."
        else:
            reminder_text = "🔕 No reminders will be sent for this task."

        response = ADD_TASK_SUCCESS.format(
            task_name=html.escape(task_name),
            task_code=task["task_code"],
            user_list=user_list,
            due_date_display=due_date_display,
            reminder_text=reminder_text,
        )

        try:
            await update.message.reply_text(response, parse_mode="HTML")
        except Exception as msg_error:
            logger.error(f"Error sending success message: {msg_error}", exc_info=True)
            await update.message.reply_text("✅ Task created successfully!")

        # Safe logging
        try:
            task_id = task.get("id", "unknown") if task else "unknown"
            logger.info(
                f"AI-parsed task created: {task_name} (ID: {task_id}) by user {user.id} (confidence: {confidence:.2f})"
            )
        except Exception as log_error:
            logger.error(f"Error in logging: {log_error}", exc_info=True)

    except ValueError as e:
        await update.message.reply_text(
            ADD_TASK_AI_ERROR.format(error=str(e)), parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error creating AI-parsed task: {e}", exc_info=True)
        await update.message.reply_text(ADD_TASK_UNEXPECTED_ERROR)


async def my_tasks_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """View your tasks: /my_tasks [new|in_progress|done|all]"""
    user = update.effective_user

    # Parse filter argument
    status_filter = None
    filter_text = ""

    if context.args:
        filter_arg = context.args[0].lower()
        if filter_arg in ["new", "in_progress", "done", "all"]:
            if filter_arg != "all":
                status_filter = filter_arg
            filter_text = f" ({filter_arg.replace('_', ' ').title()})"
        else:
            await update.message.reply_text(
                "❓ <b>Invalid filter</b>\n\n"
                "<b>Usage:</b> /my_tasks [filter]\n\n"
                "<b>Available filters:</b>\n"
                "• <code>new</code> - Only new tasks\n"
                "• <code>in_progress</code> - Only tasks in progress\n"
                "• <code>done</code> - Only completed tasks\n"
                "• <code>all</code> - All tasks (default)\n\n"
                "<b>Examples:</b>\n"
                "/my_tasks new\n"
                "/my_tasks in_progress\n"
                "/my_tasks done",
                parse_mode="HTML",
            )
            return

    tasks = database.get_user_tasks(user.id)

    # Apply status filter
    if status_filter:
        tasks = [task for task in tasks if task.get("status") == status_filter]

    if not tasks:
        if status_filter:
            await update.message.reply_text(
                f"📋 You have no {status_filter.replace('_', ' ')} tasks."
            )
        else:
            await update.message.reply_text(MY_TASKS_NONE)
        return

    # Status emoji mapping
    status_emoji = {"new": "🆕", "in_progress": "🔄", "done": "✅"}

    # Status name mapping
    status_names = {"new": "New", "in_progress": "In Progress", "done": "Done"}

    response = f"📋 <b>Your Tasks{filter_text}:</b>\n\n"

    for task in tasks:
        due_date_str = task["due_date"].strftime(DATE_FORMAT)

        if task["due_date"].tzinfo is None:
            due_date_utc = task["due_date"].replace(tzinfo=timezone.utc)
        else:
            due_date_utc = task["due_date"]

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

        # Get status display
        task_status = task.get("status", "new")
        status_display = status_emoji.get(task_status, "🆕")
        status_name = status_names.get(task_status, "New")

        response += (
            f"{status_display} <b>{task['task_code']}</b> - {task['task_name']}\n"
            f"   📊 Status: <b>{status_name}</b>\n"
            f"   ⏰ Due: {due_date_str}\n"
            f"   ⏳ Time left: {time_str}\n\n"
        )

    # Add filter hint if no filter is applied
    if not status_filter:
        response += (
            "\n💡 <i>Tip: Use /my_tasks [new|in_progress|done] to filter tasks</i>"
        )

    await update.message.reply_text(response, parse_mode="HTML")
    logger.info(
        f"User {user.id} ({user.username}) viewed their tasks (filter: {status_filter or 'all'})"
    )


async def edit_task_reminders_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    user = update.effective_user

    tasks = database.get_user_tasks(user.id)

    if not tasks:
        await update.message.reply_text(MY_TASKS_NONE)
        return

    if not context.args:
        task_lines = []
        for task in tasks:
            reminders_str = "disabled"
            if task["reminders"]:
                reminder_parts = []
                for r in task["reminders"]:
                    minutes = r["minutes_before"]
                    if minutes == 60:
                        reminder_parts.append("1h")
                    else:
                        reminder_parts.append(f"{minutes}m")
                reminders_str = ", ".join(reminder_parts)

            task_lines.append(
                f"<b>{task['task_code']}</b> - {task['task_name']}\n"
                f"   ⏰ Due: {task['due_date'].strftime(DATE_FORMAT)}\n"
                f"   🔔 Reminders: {reminders_str}\n\n"
            )

        response = EDIT_REMINDERS_USAGE.format(task_list="".join(task_lines))
        await update.message.reply_text(response, parse_mode="HTML")
        return

    try:
        task_code = context.args[0].upper()

        task = None
        for t in tasks:
            if t["task_code"].upper() == task_code:
                task = t
                break

        if not task:
            await update.message.reply_text(EDIT_REMINDERS_INVALID_TASK)
            return

        if len(context.args) < 2:
            await update.message.reply_text(EDIT_REMINDERS_NO_SETTING)
            return

        reminder_setting = context.args[1].lower()

        if reminder_setting == "off":
            success = database.update_task_reminders(
                task["id"], reminder_minutes_list=[]
            )
            if success:
                await update.message.reply_text(
                    f"✅ <b>Reminders disabled for task:</b> {task['task_name']}\n\n"
                    f"🔕 No reminders will be sent for this task.",
                    parse_mode="HTML",
                )
                logger.info(f"User {user.id} disabled reminders for task {task['id']}")
            else:
                await update.message.reply_text("❌ Error updating task reminders.")
        else:
            try:
                reminder_times_str = reminder_setting.split(",")
                reminder_minutes_list = []

                for time_str in reminder_times_str:
                    time_str = time_str.strip()
                    if not time_str:
                        continue
                    minutes = int(time_str)
                    if minutes <= 0:
                        await update.message.reply_text(EDIT_REMINDERS_NEGATIVE_TIME)
                        return
                    reminder_minutes_list.append(minutes)

                if not reminder_minutes_list:
                    await update.message.reply_text(EDIT_REMINDERS_NO_TIMES)
                    return

                success = database.update_task_reminders(
                    task["id"], reminder_minutes_list=reminder_minutes_list
                )
                if success:
                    if len(reminder_minutes_list) == 1:
                        minutes = reminder_minutes_list[0]
                        if minutes == 60:
                            time_str = TIME_1_HOUR
                        elif minutes == 30:
                            time_str = TIME_30_MINUTES
                        else:
                            time_str = f"{minutes} minutes"
                        message = EDIT_REMINDERS_UPDATED_SINGLE.format(
                            task_name=task["task_name"], time_str=time_str
                        )
                    else:
                        reminder_parts = []
                        for minutes in sorted(reminder_minutes_list):
                            if minutes == 60:
                                reminder_parts.append(TIME_1_HOUR)
                            elif minutes == 30:
                                reminder_parts.append(TIME_30_MINUTES)
                            else:
                                reminder_parts.append(f"{minutes} minutes")
                        message = EDIT_REMINDERS_UPDATED_MULTIPLE.format(
                            task_name=task["task_name"],
                            reminder_parts=", ".join(reminder_parts),
                        )

                    await update.message.reply_text(message, parse_mode="HTML")
                    logger.info(
                        f"User {user.id} updated reminders for task {task['id']} to {reminder_minutes_list}"
                    )
                else:
                    await update.message.reply_text(EDIT_REMINDERS_ERROR)
            except ValueError:
                await update.message.reply_text(EDIT_REMINDERS_INVALID_TIMES)

    except ValueError:
        await update.message.reply_text(EDIT_REMINDERS_INVALID_NUMBER)
    except Exception as e:
        logger.error(f"Error editing task reminders: {e}", exc_info=True)
        await update.message.reply_text(EDIT_REMINDERS_UPDATE_ERROR)


async def update_task_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """Update task status: /update_status <task_code> <new|in_progress|done>"""
    user = update.effective_user

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📝 <b>Update Task Status</b>\n\n"
            "<b>Usage:</b> /update_status TASK_CODE STATUS\n\n"
            "<b>Available statuses:</b>\n"
            "• <code>new</code> - Task is new (🆕)\n"
            "• <code>in_progress</code> or <code>progress</code> - Task is in progress (🔄)\n"
            "• <code>done</code> - Task is completed (✅)\n\n"
            "<b>Example:</b> /update_status TK0001 in_progress",
            parse_mode="HTML",
        )
        return

    task_code = context.args[0].upper()
    status_input = context.args[1].lower()

    # Map input to TaskStatus
    from models import TaskStatus

    status_map = {
        "new": TaskStatus.NEW,
        "in_progress": TaskStatus.IN_PROGRESS,
        "progress": TaskStatus.IN_PROGRESS,
        "done": TaskStatus.DONE,
        "complete": TaskStatus.DONE,
        "completed": TaskStatus.DONE,
    }

    if status_input not in status_map:
        await update.message.reply_text(
            f"❌ Invalid status: <code>{status_input}</code>\n\n"
            "Valid options: new, in_progress, done",
            parse_mode="HTML",
        )
        return

    new_status = status_map[status_input]

    # Get task to verify it exists and user has access
    task = database.get_task_by_code(task_code)

    if not task:
        await update.message.reply_text(
            f"❌ Task <code>{task_code}</code> not found.",
            parse_mode="HTML",
        )
        return

    # Update status
    success = database.update_task_status(task["id"], new_status)

    if success:
        status_emoji = {"new": "🆕", "in_progress": "🔄", "done": "✅"}
        emoji = status_emoji.get(new_status.value, "")

        await update.message.reply_text(
            f"{emoji} <b>Status Updated!</b>\n\n"
            f"Task: <b>{task['task_name']}</b> ({task_code})\n"
            f"New status: <b>{new_status.value.replace('_', ' ').title()}</b>",
            parse_mode="HTML",
        )
        logger.info(
            f"User {user.id} updated task {task_code} status to {new_status.value}"
        )
    else:
        await update.message.reply_text(
            "❌ Failed to update task status.",
            parse_mode="HTML",
        )


async def view_done_tasks_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """View done tasks for a user in the group (admin only): /view_done @username"""
    user = update.effective_user
    chat = update.effective_chat

    # Only works in groups
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await update.message.reply_text("⚠️ This command only works in group chats.")
        return

    # Check if user is admin
    member = await chat.get_member(user.id)
    if member.status not in ["creator", "administrator"]:
        await update.message.reply_text(
            "⚠️ Only group admins can view other users' done tasks."
        )
        return

    # Check if a user was mentioned
    mentioned_user_id = None
    mentioned_user_name = None

    # Check for text mentions
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                mentioned_user_id = entity.user.id
                mentioned_user_name = entity.user.first_name
                if entity.user.last_name:
                    mentioned_user_name += f" {entity.user.last_name}"
                break
            elif entity.type == "mention":
                # Extract @username
                username_text = update.message.text[
                    entity.offset : entity.offset + entity.length
                ]
                clean_username = username_text.lstrip("@")

                # Look up user by username
                user_id = database.get_user_by_username(clean_username)
                if user_id:
                    mentioned_user_id = user_id
                    user_info = database.get_user_by_telegram_id(user_id)
                    if user_info:
                        if user_info.get("username"):
                            mentioned_user_name = f"@{user_info['username']}"
                        else:
                            mentioned_user_name = user_info.get("first_name", "User")
                            if user_info.get("last_name"):
                                mentioned_user_name += f" {user_info['last_name']}"
                break

    if not mentioned_user_id:
        await update.message.reply_text(
            "📋 <b>View Done Tasks</b>\n\n"
            "<b>Usage:</b> /view_done @username or tap on user's name\n\n"
            "This will show all completed tasks for that user in this group.",
            parse_mode="HTML",
        )
        return

    # Get done tasks for the user in this chat
    done_tasks = database.get_done_tasks_for_user_in_chat(mentioned_user_id, chat.id)

    if not done_tasks:
        await update.message.reply_text(
            f"📭 No completed tasks found for {mentioned_user_name} in this group.",
            parse_mode="HTML",
        )
        return

    # Build response
    response = f"✅ <b>Completed Tasks for {html.escape(mentioned_user_name)}</b>\n\n"

    for task in done_tasks:
        due_date_str = task["due_date"].strftime(DATE_FORMAT)

        response += (
            f"<b>{task['task_code']}</b> - {html.escape(task['task_name'])}\n"
            f"   ⏰ Due: {due_date_str}\n"
            f"   📅 Created: {task['created_at'].strftime(DATE_FORMAT)}\n\n"
        )

    await update.message.reply_text(response, parse_mode="HTML")
    logger.info(
        f"Admin {user.id} viewed done tasks for user {mentioned_user_id} in chat {chat.id}"
    )


async def delete_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, database
):
    """Delete one or more tasks (admin only): /delete_task <task_code1> [task_code2] ..."""
    user = update.effective_user
    chat = update.effective_chat

    # Only works in groups
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await update.message.reply_text("⚠️ This command only works in group chats.")
        return

    # Check if user is admin
    member = await chat.get_member(user.id)
    if member.status not in ["creator", "administrator"]:
        await update.message.reply_text("⚠️ Only group admins can delete tasks.")
        return

    if not context.args:
        await update.message.reply_text(
            "🗑️ <b>Delete Tasks</b>\n\n"
            "<b>Usage:</b> /delete_task TASK_CODE [TASK_CODE2] ...\n\n"
            "<b>Examples:</b>\n"
            "• /delete_task TK0001\n"
            "• /delete_task TK0001 TK0002 TK0003\n"
            "• /delete_task TK0001,TK0002\n\n"
            "⚠️ <b>Warning:</b> This action cannot be undone!",
            parse_mode="HTML",
        )
        return

    # Parse task codes - handle both space-separated and comma-separated
    task_codes_input = " ".join(context.args)
    # Split by comma first, then by space for each part
    task_codes = []
    for part in task_codes_input.split(","):
        task_codes.extend(
            [code.strip().upper() for code in part.split() if code.strip()]
        )

    # Remove duplicates while preserving order
    seen = set()
    task_codes = [x for x in task_codes if not (x in seen or seen.add(x))]

    if not task_codes:
        await update.message.reply_text(
            "❌ No valid task codes provided.",
            parse_mode="HTML",
        )
        return

    # Validate and collect tasks
    valid_tasks = []
    invalid_codes = []
    wrong_chat_codes = []

    for task_code in task_codes:
        task = database.get_task_by_code(task_code)

        if not task:
            invalid_codes.append(task_code)
            continue

        # Verify task is from this chat
        if task["chat_id"] != chat.id:
            wrong_chat_codes.append(task_code)
            continue

        valid_tasks.append(task)

    # Report issues
    if invalid_codes:
        await update.message.reply_text(
            f"❌ The following task codes were not found: <code>{', '.join(invalid_codes)}</code>",
            parse_mode="HTML",
        )

    if wrong_chat_codes:
        await update.message.reply_text(
            f"❌ The following tasks are not from this group: <code>{', '.join(wrong_chat_codes)}</code>",
            parse_mode="HTML",
        )

    if not valid_tasks:
        return

    # Delete valid tasks
    deleted_tasks = []
    failed_deletions = []

    for task in valid_tasks:
        success = database.delete_task(task["id"])
        if success:
            deleted_tasks.append(task)
        else:
            failed_deletions.append(task["task_code"])

    # Report results
    if deleted_tasks:
        if len(deleted_tasks) == 1:
            task = deleted_tasks[0]
            await update.message.reply_text(
                f"🗑️ <b>Task Deleted!</b>\n\n"
                f"Task <code>{task['task_code']}</code> - <b>{html.escape(task['task_name'])}</b> has been permanently deleted.",
                parse_mode="HTML",
            )
        else:
            task_list = "\n".join(
                [
                    f"• <code>{task['task_code']}</code> - {html.escape(task['task_name'])}"
                    for task in deleted_tasks
                ]
            )
            await update.message.reply_text(
                f"🗑️ <b>{len(deleted_tasks)} Tasks Deleted!</b>\n\n"
                f"The following tasks have been permanently deleted:\n{task_list}",
                parse_mode="HTML",
            )
        logger.info(
            f"Admin {user.id} deleted {len(deleted_tasks)} tasks in chat {chat.id}: {[t['task_code'] for t in deleted_tasks]}"
        )

    if failed_deletions:
        await update.message.reply_text(
            f"❌ Failed to delete the following tasks: <code>{', '.join(failed_deletions)}</code>",
            parse_mode="HTML",
        )
