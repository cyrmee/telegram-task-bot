# Telegram Task Management Bot

A Python-based Telegram bot for managing and scheduling group tasks with automated reminders.

## Features

- ✅ **Task Management**: Create and assign tasks to group members
- ⏰ **Automated Reminders**: Get notified 30 minutes before task deadlines
- 🔒 **Privacy First**: Users must explicitly opt-in to receive reminders
- 👥 **Admin Controls**: Only group administrators can create tasks
- 📊 **Personal Dashboard**: View all your assigned tasks
- 💾 **Persistent Storage**: PostgreSQL database (hosted on Neon) for reliable data storage

## Requirements

- Python 3.8+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Google Gemini API Key (from [Google AI Studio](https://aistudio.google.com/app/apikey))

## Installation

1. **Clone the repository**:

   ```bash
   git clone <repository-url>
   cd telegram-task-bot
   ```

2. **Create a virtual environment** (recommended):

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Edit `.env` and add your API keys:
     ```
     TELEGRAM_BOT_TOKEN=your_bot_token_here
     GEMINI_API_KEY=your_gemini_api_key_here
     ```

## Usage

### Starting the Bot

#### Polling Mode (Default)

```bash
python bot.py
# or
python main.py
```

#### Webhook Mode (Production)

```bash
# Set environment variable
export RUN_MODE=webhook
python main.py
```

For webhook mode, you need a public URL. Use ngrok for development:

```bash
# Install ngrok and run
ngrok http 8000
# Then set webhook URL with Telegram API
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_NGROK_URL>/webhook/<BOT_ID>"
```

#### Combined Mode (API + Polling)

```bash
export RUN_MODE=combined
python main.py
```

### REST API

The bot includes a FastAPI-based REST API for programmatic access:

- **Base URL**: `http://localhost:8000` (development)
- **Documentation**: `http://localhost:8000/docs` (Swagger UI)
- **Alternative Docs**: `http://localhost:8000/redoc`

#### API Endpoints

- `GET /` - API status
- `POST /users/` - Create user
- `GET /users/{telegram_id}` - Get user
- `GET /users/` - List users
- `POST /tasks/` - Create task
- `GET /tasks/` - List tasks
- `GET /tasks/{task_id}` - Get task
- `PUT /tasks/{task_id}` - Update task
- `DELETE /tasks/{task_id}` - Delete task
- `POST /tasks/{task_id}/assign/{telegram_id}` - Assign task to user
- `POST /webhook/{token}` - Telegram webhook endpoint

### Available Commands

#### For All Users:

- `/start` - Register your account with the bot
- `/receive_reminders` - Opt-in to receive task reminders (must be sent in private chat)
- `/my_tasks` - View all tasks assigned to you

#### For Group Administrators:

- `/add_task [natural language description]` - Create a new task using AI-powered natural language parsing

**Examples**:

```
✅ Natural language examples:
/add_task Prepare the quarterly report for @john and @jane, due tomorrow at 2 PM
/add_task @mike needs to finish the website design by next Friday
/add_task Code review for the new feature with @sarah and @tom, deadline is 2025-10-25 15:00

❌ Old strict format (still works but not recommended):
/add_task "Prepare quarterly report" @john @jane 2025-10-20 14:30
```

## How It Works

### Task Creation Flow

1. Group admin uses `/add_task` command with natural language description in a group chat
2. AI parses the description to extract task name, assigned users, and deadline
3. Bot validates admin permissions and user mentions
4. Task is stored in PostgreSQL database with assigned users and deadline
5. Confirmation message shows AI confidence level and parsed details

### Reminder System

1. Scheduler checks for tasks every minute
2. When a task is 30 minutes away from deadline:
   - Bot sends reminder to the group
   - Only users who opted-in with `/receive_reminders` are mentioned
   - Task is marked as "reminder sent" to avoid duplicates

### Privacy & Opt-in

- Users must send `/receive_reminders` in a **private chat** with the bot
- This ensures explicit consent for notifications
- Users can be assigned tasks even if they haven't opted in, but won't receive reminders

## Project Structure

```
telegram-task-bot/
├── main.py                 # Main entry point with multiple run modes
├── bot.py                  # Telegram bot logic
├── api.py                  # FastAPI REST API
├── database.py             # SQLAlchemy models and database operations
├── scheduler.py            # APScheduler for automated reminders
├── handlers/
│   ├── __init__.py
│   └── commands.py         # Command handlers
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
├── .gitignore             # Git ignore rules
└── README.md              # This file
```

## Database Schema

### Users Table

- `id` (Primary Key) - Telegram user ID
- `username` - Telegram username
- `first_name` - User's first name
- `last_name` - User's last name
- `receive_reminders` - Boolean flag for opt-in status
- `created_at` - Registration timestamp

### Tasks Table

- `id` (Primary Key) - Auto-incrementing task ID
- `task_name` - Task description
- `chat_id` - Group chat ID where task was created
- `due_date` - Task deadline
- `created_at` - Creation timestamp
- `reminder_sent` - Boolean flag to track reminder status
- `completed` - Boolean flag for task completion

### Task Assignments (Many-to-Many)

- Links tasks to assigned users

## Technologies Used

- **python-telegram-bot** (v20+): Async Telegram Bot API wrapper
- **APScheduler**: Advanced Python Scheduler for periodic tasks
- **SQLAlchemy**: SQL toolkit and ORM
- **PostgreSQL**: Powerful, open source object-relational database system
- **python-dotenv**: Environment variable management

## Development Notes

- All times are stored and processed in **UTC**
- The scheduler uses an interval trigger to check every minute
- Database sessions are properly managed with try/finally blocks
- Comprehensive logging is implemented throughout

## Troubleshooting

### Bot doesn't respond

- Check if your bot token is correct in `.env`
- Ensure the bot is running (`python bot.py`)
- Verify you've started a conversation with the bot

### Reminders not sending

- Check that users have used `/receive_reminders` in private chat
- Verify task due date is in the future
- Check logs for any errors

### Users not found when adding tasks

- Ensure mentioned users have used `/start` command first
- Users must be registered in the database before being assigned tasks

## License

This project is open source and available under the MIT License.

## Support

For issues, questions, or contributions, please open an issue on GitHub.
