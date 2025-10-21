from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatType
from datetime import datetime, timezone
import re
import logging

from constants import (
    DATE_FORMAT,
    START_MESSAGE,
    HELP_MESSAGE,
    ADD_TASK_GROUP_ONLY,
    ADD_TASK_ADMIN_ONLY,
    ADD_TASK_NO_DESCRIPTION,
    ADD_TASK_PAST_DATE,
    ADD_TASK_NO_USERS,
    ADD_TASK_USERS_NOT_FOUND,
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
                    "id": u.id,
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

        if not usernames:
            await update.message.reply_text(ADD_TASK_NO_USERS)
            return

        assigned_user_ids = []
        unregistered_usernames = []
        session = database.get_session()
        try:
            from models import User

            for username in usernames:
                user_obj = session.query(User).filter_by(username=username).first()
                if user_obj:
                    assigned_user_ids.append(user_obj.id)
                else:
                    unregistered_usernames.append(username)
        finally:
            session.close()

        # Send registration requests to unregistered users
        if unregistered_usernames:
            mentions = " ".join([f"@{username}" for username in unregistered_usernames])
            await update.message.reply_text(
                f"{mentions} Please start me with /start so I can assign tasks to you!",
                reply_to_message_id=update.message.message_id,
            )

        if not assigned_user_ids:
            if unregistered_usernames:
                await update.message.reply_text(
                    "⚠️ All mentioned users need to register first. I've sent them instructions to use /start.\n\n"
                    "Please wait for them to register, then try creating the task again."
                )
            else:
                await update.message.reply_text(
                    "⚠️ No registered users found for the mentioned usernames.\n\n"
                    "Please ask these users to use /start to register with the bot first:\n"
                    f"{', '.join([f'@{u}' for u in usernames])}\n\n"
                    "Then try creating the task again."
                )
            return

        task = database.add_task(
            task_name=task_name,
            chat_id=chat.id,
            due_date=due_date,
            assigned_user_ids=assigned_user_ids,
            reminder_minutes_list=reminder_minutes_list,
        )

        user_list = ", ".join([f"@{u}" for u in usernames])
        due_date_display = due_date.strftime(DATE_FORMAT)

        if confidence > 0.8:
            confidence_icon = "🎯"
        elif confidence > 0.6:
            confidence_icon = "⚠️"
        else:
            confidence_icon = "❓"
        confidence_text = f"{confidence_icon} AI confidence: {confidence:.1%}"

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
            task_name=task_name,
            task_code=task["task_code"],
            user_list=user_list,
            due_date_display=due_date_display,
            reminder_text=reminder_text,
            confidence_text=confidence_text,
        )

        await update.message.reply_text(response, parse_mode="HTML")
        logger.info(
            f"AI-parsed task created: {task_name} (ID: {task['id']}) by user {user.id} (confidence: {confidence:.2f})"
        )

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
    user = update.effective_user

    tasks = database.get_user_tasks(user.id)

    if not tasks:
        await update.message.reply_text(MY_TASKS_NONE)
        return

    response = "📋 <b>Your Active Tasks:</b>\n\n"

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

        response += (
            f"<b>{task['task_code']}</b> - {task['task_name']}\n"
            f"   ⏰ Due: {due_date_str}\n"
            f"   ⏳ Time left: {time_str}\n\n"
        )

    await update.message.reply_text(response, parse_mode="HTML")
    logger.info(f"User {user.id} ({user.username}) viewed their tasks")


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
