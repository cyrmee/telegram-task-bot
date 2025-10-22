import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATE_FORMAT = "%Y-%m-%d %H:%M UTC"

AI_MODEL = "gemini-2.5-flash"
AI_TEMPERATURE = 0.1
AI_MIME_TYPE = "application/json"
AI_REQUIRED_KEYS = [
    "task_name",
    "usernames",
    "due_date_relative",
    "due_time",
    "reminder_minutes_list",
    "confidence",
]

DATABASE_URL = os.environ.get("DATABASE_URL")

SCHEDULER_INTERVAL_MINUTES = 1

BOT_COMMANDS = [
    ("start", "Register/update your profile"),
    ("add_task", "Add a new task (admins only, in groups)"),
    ("my_tasks", "View your tasks (optional: filter by status)"),
    ("update_status", "Update task status (new/in_progress/done)"),
    ("view_done", "View completed tasks for a user (admins only)"),
    ("delete_task", "Delete a task (admins only)"),
    ("edit_task_reminders", "Customize reminder settings for your tasks"),
    ("help", "Get help using the bot"),
]

AI_PROMPT_TEMPLATE = """
You are a task parsing assistant for a Telegram bot. Parse the following natural language task description into structured JSON format.

Available users in the group:
{user_list_text}

Task description: "{text}"

Please extract:
1. task_name: The name/description of the task
2. usernames: Array of usernames/display names mentioned (without @ symbol) - match against available users by username or display name
3. due_date_relative: Relative date description (e.g., "tomorrow", "next week", "in 3 days", "today", "next monday")
4. due_time: Time in HH:MM format (24-hour format, default to "09:00" if not specified)
5. reminder_minutes_list: Array of minutes before due date to send reminders (default [30] if not specified)
6. confidence: Your confidence level (0.0-1.0) in the parsing accuracy

Rules:
- If no specific date is mentioned, use "tomorrow"
- If no specific time is mentioned, use "09:00"
- If no users are mentioned, return empty usernames array
- Match usernames against both @username and display names (first_name last_name)
- Only include usernames/display names that exist in the available users list
- For reminders: parse multiple reminder times like "remind me 1 hour before, 30 minutes before, and 15 minutes before"
- Convert reminder times to minutes: 1 hour = 60 minutes, 30 minutes = 30 minutes, etc.
- If "no reminders" or "don't remind" is mentioned, set reminder_minutes_list to empty array []
- Default reminder_minutes_list is [30] (30 minutes before)
- Return valid JSON only

Example output:
{{
    "task_name": "Prepare presentation",
    "usernames": ["john", "jane"],
    "due_date_relative": "tomorrow",
    "due_time": "14:30",
    "reminder_minutes_list": [60, 30, 15],
    "confidence": 0.95
}}
"""

START_MESSAGE = (
    "👋 Hello {user_first_name}!\n\n"
    "Welcome to the Task Management Bot!\n\n"
    "<b>Available Commands:</b>\n"
    "• /start - Register/update your profile\n"
    "• /add_task - Add a new task (admins only, in groups)\n"
    "• /my_tasks - View your assigned tasks\n"
    "• /edit_task_reminders - Customize reminder settings for your tasks\n\n"
    "<b>Note:</b> You will receive reminders by default. "
    "Use /edit_task_reminders to customize or disable them!"
)

HELP_MESSAGE = (
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
    "• /add_task Code review with @sarah and @tom, deadline is 2025-10-25 15:00\n\n"
    "<b>Task Codes:</b>\n"
    "Each task has a unique code (e.g., TK0001) for easy reference.\n\n"
    "<b>Reminder Customization:</b>\n"
    "• /edit_task_reminders &lt;task_code&gt; &lt;reminder_times&gt;\n\n"
    "Examples:\n"
    "• /edit_task_reminders TK0001 60,30,15 (remind at 1h, 30m, 15m before)\n"
    "• /edit_task_reminders TK0001 120 (remind 2 hours before)\n"
    "• /edit_task_reminders TK0001 off (disable reminders)\n\n"
    "<b>Note:</b> You will receive reminders by default. Use /edit_task_reminders to customize or disable them!"
)

ADD_TASK_GROUP_ONLY = (
    "⚠️ Sorry, this command only works in group chats. Please try it in a group!"
)
ADD_TASK_ADMIN_ONLY = "⚠️ Only group admins can create tasks. Ask an admin to help you!"
ADD_TASK_NO_DESCRIPTION = (
    "❌ <b>Please tell me what the task is!</b>\n\n"
    "<b>Examples:</b>\n"
    "• /add_task Prepare quarterly report for @john, due tomorrow at 2 PM\n"
    "• /add_task @mike needs to finish the website design by next Friday\n"
    "• /add_task Code review with @sarah and @tom, deadline is 2025-10-25 15:00\n\n"
    "<b>Tip:</b> Mention people with @username and use natural dates like 'tomorrow' or 'next week'!"
)
ADD_TASK_PAST_DATE = (
    "⚠️ The due date needs to be in the future. I understood: {due_date_str}"
)
ADD_TASK_AI_ERROR = (
    "❌ <b>I had trouble understanding your task.</b> {error}\n\n"
    "<b>Try saying it differently. Examples:</b>\n"
    "• /add_task Prepare quarterly report for @john, due tomorrow at 2 PM\n"
    "• /add_task @mike needs to finish the website design by next Friday\n"
    "• /add_task Code review with @sarah and @tom, deadline is 2025-10-25 15:00"
)
ADD_TASK_UNEXPECTED_ERROR = "❌ Something went wrong while creating your task. Please try again with simpler words!"
ADD_TASK_SUCCESS = (
    "✅ <b>Task Created!</b>\n\n"
    "📋 <b>Task:</b> {task_name}\n"
    "🔢 <b>Task Code:</b> {task_code}\n"
    "👥 <b>Assigned to:</b> {user_list}\n"
    "⏰ <b>Due:</b> {due_date_display}\n"
    "{reminder_text}\n"
)
MY_TASKS_NONE = "📭 You have no active tasks assigned to you."

EDIT_REMINDERS_USAGE = (
    "📋 <b>Your Tasks & Reminder Settings:</b>\n\n"
    "Use: /edit_task_reminders &lt;task_code&gt; &lt;reminder_times&gt;\n\n"
    "Examples:\n"
    "• /edit_task_reminders TK0001 60,30,15 (remind at 1h, 30m, 15m before)\n"
    "• /edit_task_reminders TK0001 120 (remind 2 hours before)\n"
    "• /edit_task_reminders TK0001 off (disable reminders)\n\n"
    "{task_list}"
)

EDIT_REMINDERS_INVALID_TASK = (
    "❌ That task number doesn't exist. Please check your task list!"
)
EDIT_REMINDERS_NO_SETTING = (
    "❌ Please tell me how you want reminders set up.\n\n"
    "Examples:\n"
    "• /edit_task_reminders 1 60,30,15 (remind 1 hour, 30 mins, 15 mins before)\n"
    "• /edit_task_reminders 1 120 (remind 2 hours before)\n"
    "• /edit_task_reminders 1 off (turn off reminders)"
)
EDIT_REMINDERS_NEGATIVE_TIME = (
    "❌ Reminder times need to be positive numbers greater than zero."
)
EDIT_REMINDERS_NO_TIMES = "❌ Please include at least one reminder time."
EDIT_REMINDERS_INVALID_TIMES = (
    "❌ I didn't understand those reminder times. Please use numbers separated by commas.\n\n"
    "Examples:\n"
    "• /edit_task_reminders 1 60,30,15\n"
    "• /edit_task_reminders 1 120\n"
    "• /edit_task_reminders 1 off"
)
EDIT_REMINDERS_DISABLED = (
    "✅ <b>Reminders turned off for:</b> {task_name}\n\n"
    "🔕 You won't get any reminders for this task."
)
EDIT_REMINDERS_UPDATED_SINGLE = (
    "✅ <b>Reminder set for:</b> {task_name}\n\n"
    "🔔 You'll be reminded {time_str} before it's due."
)
EDIT_REMINDERS_UPDATED_MULTIPLE = (
    "✅ <b>Reminders set for:</b> {task_name}\n\n"
    "🔔 You'll be reminded {reminder_parts} before it's due."
)
EDIT_REMINDERS_ERROR = "❌ Sorry, I couldn't update the reminders. Please try again."
EDIT_REMINDERS_INVALID_NUMBER = (
    "❌ Please use a valid task code. For example: /edit_task_reminders TK0001 ..."
)
EDIT_REMINDERS_UPDATE_ERROR = (
    "❌ Something went wrong updating your reminders. Please try again."
)

TIME_1_HOUR = "1 hour"
TIME_30_MINUTES = "30 minutes"

REMINDER_MESSAGE = (
    "🔔 <b>Task Reminder</b>\n\n"
    "📋 <b>Task:</b> {task_name}\n"
    "🔢 <b>Task Code:</b> {task_code}\n"
    "⏰ <b>Due:</b> {due_date_str}\n"
    "👥 <b>Assigned to:</b> {user_mentions}\n\n"
    "⚠️ This task is due in about {time_str}!"
)
