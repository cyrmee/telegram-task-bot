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
    ("register", "Register/update your profile (same as /start)"),
    ("add_task", "Add a new task (admins only, in groups)"),
    ("my_tasks", "View your tasks (optional: filter by status)"),
    (
        "list_tasks",
        "List all tasks for a user (admins only, mandatory username, optional status)",
    ),
    ("update_status", "Update task status (new/in_progress/done)"),
    ("delete_task", "Delete one or more tasks (admins only)"),
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
    "Hello {user_first_name}, you are registered.\n\n"
    "Admins can assign tasks to you using @username mentions.\n\n"
    "Tip: Use /help for available commands."
)

HELP_MESSAGE = (
    "<b>Task Management Bot Help</b>\n\n"
    "Commands:\n"
    "/start or /register - Register/update profile\n"
    "/add_task - Add task (admins only, in groups)\n"
    "/my_tasks - View assigned tasks\n"
    "/edit_task_reminders - Customize reminders\n"
    "/help - Show this message\n\n"
    "Examples:\n"
    "/add_task Prepare report for @john, due tomorrow at 2 PM\n"
    "/edit_task_reminders TK0001 60,30 (remind 1h and 30m before)\n\n"
    "Tip: Mention users with @username and use natural dates."
)

ADD_TASK_GROUP_ONLY = "This command only works in group chats."
ADD_TASK_ADMIN_ONLY = "Only group admins can create tasks."
ADD_TASK_NO_DESCRIPTION = (
    "Please provide a task description.\n\n"
    "Example: /add_task Prepare report for @john, due tomorrow at 2 PM\n\n"
    "Tip: Mention users with @username and use natural dates."
)
ADD_TASK_PAST_DATE = "The due date must be in the future. Detected: {due_date_str}"
ADD_TASK_AI_ERROR = (
    "Unable to parse task: {error}\n\n"
    "Try rephrasing. Example: /add_task Prepare report for @john, due tomorrow at 2 PM"
)
ADD_TASK_UNEXPECTED_ERROR = (
    "An error occurred while creating the task. Please try again."
)
ADD_TASK_SUCCESS = (
    "Task created.\n\n"
    "Task: {task_name}\n"
    "Code: {task_code}\n"
    "Assigned to: {user_list}\n"
    "Due: {due_date_display}\n"
    "{reminder_text}\n\n"
    "Tip: Use /my_tasks to view your tasks."
)
MY_TASKS_NONE = "You have no active tasks."

EDIT_REMINDERS_USAGE = (
    "Your tasks and reminder settings:\n\n"
    "Usage: /edit_task_reminders <task_code> <reminder_times>\n\n"
    "Examples:\n"
    "/edit_task_reminders TK0001 60,30,15 (1h, 30m, 15m before)\n"
    "/edit_task_reminders TK0001 off (disable)\n\n"
    "{task_list}\n\n"
    "Tip: Separate times with commas, use 'off' to disable."
)

EDIT_REMINDERS_INVALID_TASK = "Task code not found. Check your task list."
EDIT_REMINDERS_NO_SETTING = (
    "Specify reminder settings.\n\n"
    "Examples:\n"
    "/edit_task_reminders 1 60,30,15\n"
    "/edit_task_reminders 1 off"
)
EDIT_REMINDERS_NEGATIVE_TIME = "Reminder times must be positive numbers."
EDIT_REMINDERS_NO_TIMES = "Include at least one reminder time."
EDIT_REMINDERS_INVALID_TIMES = (
    "Invalid reminder times. Use numbers separated by commas.\n\n"
    "Examples:\n"
    "/edit_task_reminders 1 60,30,15\n"
    "/edit_task_reminders 1 off"
)
EDIT_REMINDERS_DISABLED = (
    "Reminders disabled for: {task_name}\n\n"
    "Tip: Use /edit_task_reminders to re-enable."
)
EDIT_REMINDERS_UPDATED_SINGLE = (
    "Reminder set for: {task_name}\n\n" "Reminder: {time_str} before due date."
)
EDIT_REMINDERS_UPDATED_MULTIPLE = (
    "Reminders set for: {task_name}\n\n" "Reminders: {reminder_parts} before due date."
)
EDIT_REMINDERS_ERROR = "Failed to update reminders. Try again."
EDIT_REMINDERS_INVALID_NUMBER = (
    "Use a valid task code, e.g., /edit_task_reminders TK0001 ..."
)
EDIT_REMINDERS_UPDATE_ERROR = "Error updating reminders. Try again."

TIME_1_HOUR = "1 hour"
TIME_30_MINUTES = "30 minutes"

GROUP_ONLY_MESSAGE = "This command only works in group chats."

REMINDER_MESSAGE = (
    "<b>Task Reminder</b>\n\n"
    "<b>Task:</b> {task_name}\n"
    "<b>Task Code:</b> {task_code}\n"
    "<b>Due:</b> {due_date_str}\n"
    "<b>Assigned to:</b> {user_mentions}\n\n"
    "This task is due in about {time_str}!"
)
