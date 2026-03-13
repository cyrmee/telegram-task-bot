import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler

from database import Database
from scheduler import TaskScheduler
from ai_parser import TaskParser
from models import TaskStatus
from handlers.commands import (
    start_command,
    register_command,
    add_task_command,
    my_tasks_command,
    list_tasks_command,
    edit_task_reminders_command,
    update_task_status_command,
    delete_task_command,
    help_command,
)
from constants import BOT_COMMANDS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Basic Pydantic Schemas for API
class UserResponse(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    receive_reminders: bool

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    projectId: Optional[int] = None
    assigneeId: int
    dueDate: datetime

class TaskUpdate(BaseModel):
    status: TaskStatus

class MemberInvite(BaseModel):
    telegramId: str
    username: str
    workspaceId: str

class WorkspaceTokenUpdate(BaseModel):
    token: str

class TaskBot:
    def __init__(self, token: str):
        self.token = token
        self.database = Database()
        self.ai_parser = TaskParser()
        self.application = Application.builder().token(self.token).build()
        self.scheduler = None
        self.initialized = False

        self.setup_handlers()

    def setup_handlers(self):
        async def start_wrapper(update, context):
            await start_command(update, context, self.database)

        async def register_wrapper(update, context):
            await register_command(update, context, self.database)

        async def add_task_wrapper(update, context):
            await add_task_command(update, context, self.database, self.ai_parser)

        async def my_tasks_wrapper(update, context):
            await my_tasks_command(update, context, self.database)

        async def list_tasks_wrapper(update, context):
            await list_tasks_command(update, context, self.database)

        async def edit_task_reminders_wrapper(update, context):
            await edit_task_reminders_command(update, context, self.database)

        async def update_status_wrapper(update, context):
            await update_task_status_command(update, context, self.database)

        async def delete_task_wrapper(update, context):
            await delete_task_command(update, context, self.database)

        async def help_wrapper(update, context):
            await help_command(update, context)

        self.application.add_handler(CommandHandler("start", start_wrapper))
        self.application.add_handler(CommandHandler("register", register_wrapper))
        self.application.add_handler(CommandHandler("add_task", add_task_wrapper))
        self.application.add_handler(CommandHandler("my_tasks", my_tasks_wrapper))
        self.application.add_handler(CommandHandler("list_tasks", list_tasks_wrapper))
        self.application.add_handler(
            CommandHandler("edit_task_reminders", edit_task_reminders_wrapper)
        )
        self.application.add_handler(
            CommandHandler("update_status", update_status_wrapper)
        )
        self.application.add_handler(CommandHandler("delete_task", delete_task_wrapper))
        self.application.add_handler(CommandHandler("help", help_wrapper))

        logger.info("Command handlers registered")

    async def initialize(self):
        await self.application.initialize()

        # Set bot instance in database for fetching user info
        self.database.set_bot(self.application.bot)

        # Try to set bot commands (may fail if Telegram API is unreachable)
        commands = [
            BotCommand(command, description) for command, description in BOT_COMMANDS
        ]

        try:
            await asyncio.wait_for(
                self.application.bot.set_my_commands(commands),
                timeout=10
            )
            logger.info("Bot commands set successfully")
        except asyncio.TimeoutError:
            logger.warning("Timed out setting bot commands (Telegram API may be unreachable)")
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

        self.scheduler = TaskScheduler(self.application.bot, self.database)
        self.scheduler.start()
        logger.info("Task scheduler initialized and started")

        # Webhook setup
        webhook_url = os.getenv("WEBHOOK_URL")
        if webhook_url:
            full_webhook_url = f"{webhook_url.rstrip('/')}/webhook"
            try:
                await self.application.bot.set_webhook(url=full_webhook_url, drop_pending_updates=True)
                logger.info(f"Webhook set to: {full_webhook_url}")
            except Exception as e:
                logger.error(f"Failed to set webhook: {e}")
        else:
            logger.warning("WEBHOOK_URL not found in environment. Bot will NOT receive updates unless you use polling.")

        await self.application.start()
        self.initialized = True

    async def shutdown(self):
        if self.scheduler:
            self.scheduler.shutdown()
        if self.initialized:
            await self.application.stop()
            await self.application.shutdown()
        logger.info("Bot shutdown complete")


# ---------------------------------------------------------
# FastAPI App Definition
# ---------------------------------------------------------
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")

bot_instance = TaskBot(token) if token else None

@asynccontextmanager
async def lifespan(app: FastAPI):
    if bot_instance:
        try:
            await asyncio.wait_for(bot_instance.initialize(), timeout=15)
            logger.info("Bot initialized successfully")
        except asyncio.TimeoutError:
            logger.warning("Bot initialization timed out — Telegram API may be unreachable. REST API will still work.")
        except Exception as e:
            logger.warning(f"Bot initialization failed: {e}. REST API will still work.")
    yield
    if bot_instance:
        await bot_instance.shutdown()

app = FastAPI(title="Task Bot API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://nest-dashboard-iq2x.onrender.com"
        ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    try:
        data = await request.json()
        update = Update.de_json(data, bot_instance.application.bot)
        await bot_instance.application.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/auth/login")
async def auth_login(request: Request):
    # Basic mock local login for dashboard initialization
    return {
        "success": True,
        "managerId": "manager_01", # Replace with real DB ID
        "workspaceId": "ws_01"      # Replace with real DB ID
    }
    # return {
    #     "status": "success",
    #     "message": "Session initialized",
    #     "user": {
    #         "id": 1,
    #         "username": "admin",
    #         "role": "administrator"
    #     },
    #     "token": "mock-dev-token"
    # }

# REST API ENDPOINTS

@app.get("/api/users")
async def get_users():
    if not bot_instance:
        return []
    session = bot_instance.database.get_session()
    try:
        from models import User
        users = session.query(User).all()
        return [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "receive_reminders": u.receive_reminders,
            }
            for u in users
        ]
    finally:
        bot_instance.database.close_session(session)

@app.get("/api/projects", response_model=List[ProjectResponse])
async def get_projects():
    if not bot_instance:
        return []
    projects = bot_instance.database.get_projects()
    # Ensure ID is string if frontend expects it, though ProjectResponse has it as int.
    # Actually, TasksPage.tsx interface has id: string. Let's be safe.
    return [{**p, "id": str(p["id"])} for p in projects]

@app.post("/api/projects", response_model=ProjectResponse)
async def create_project(project: ProjectCreate):
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    return bot_instance.database.add_project(name=project.name, description=project.description)

@app.get("/api/tasks")
async def get_tasks(projectId: Optional[str] = None, assigneeId: Optional[str] = None):
    if not bot_instance:
        return []
    session = bot_instance.database.get_session()
    try:
        from models import Task, User, Project, TaskAssignment
        from sqlalchemy.orm import joinedload
        
        query = session.query(Task).options(
            joinedload(Task.project),
            joinedload(Task.assignments).joinedload(TaskAssignment.user)
        )

        if projectId:
            query = query.filter(Task.project_id == int(projectId))
        
        if assigneeId:
            # Filter by the assigned user's telegram_id
            query = query.join(TaskAssignment).filter(TaskAssignment.user_id == int(assigneeId))
        
        tasks = query.all()
        
        result = []
        for t in tasks:
            # Map DB status (NEW, IN_PROGRESS, DONE) to Frontend status (PENDING, COMPLETED)
            fe_status = "COMPLETED" if t.status == TaskStatus.DONE else "PENDING"
            
            # For simplicity in this dashboard, we'll take the first assignee
            first_assignee = t.assignments[0].user if t.assignments else None
            
            result.append({
                "id": str(t.id),
                "title": t.task_name,
                "status": fe_status,
                "dueDate": t.due_date.isoformat(),
                "projectId": str(t.project_id) if t.project_id else None,
                "assigneeId": str(first_assignee.telegram_id) if first_assignee else None,
                "project": {"name": t.project.name} if t.project else None,
                "assignee": {
                    "username": first_assignee.username if first_assignee else "Unassigned",
                    "telegramId": str(first_assignee.telegram_id) if first_assignee else None
                } if first_assignee else None,
                "task_code": t.task_code,
                "completed": t.completed,
                "created_at": t.created_at,
            })
        return result
    finally:
        bot_instance.database.close_session(session)

@app.post("/api/tasks")
async def create_task(task: TaskCreate):
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    # Map frontend fields to database fields
    # Using assigneeId as chat_id for direct DMs as per current system logic
    new_task = bot_instance.database.add_task(
        task_name=task.title,
        chat_id=task.assigneeId,
        due_date=task.dueDate,
        assigned_user_ids=[task.assigneeId],
        workspace_id="ws_01", # Default or from header if implemented
        project_id=task.projectId,
    )

    # Cross-communication: DM assigned user
    try:
        message_text = (
            f"📝 *New Task Assigned to You*\n\n"
            f"*Task:* {task.title}\n"
            f"*Due Date:* {task.dueDate.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Please make sure to complete this on time!"
        )
        await bot_instance.application.bot.send_message(
            chat_id=task.assigneeId,
            text=message_text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send DM to user {task.assigneeId}: {e}")

    return new_task

@app.patch("/api/tasks/{task_id}")
async def update_task_api(task_id: int, task_update: TaskUpdate):
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    success = bot_instance.database.update_task_status(task_id, task_update.status)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task status updated", "status": task_update.status.value}

# @app.get("/api/analytics")
# async def get_analytics():
#     if not bot_instance:
#         return {}
#     return bot_instance.database.get_analytics()

@app.get("/api/members")
async def get_members():
    if not bot_instance:
        return []
    session = bot_instance.database.get_session()
    try:
        from models import User
        users = session.query(User).all()
        return [
            {
                "id": str(u.telegram_id), # Map telegram_id to id for frontend keys
                "username": u.username or f"User_{u.telegram_id}",
                "first_name": u.first_name,
                "last_name": u.last_name
            } for u in users
        ]
    finally:
        bot_instance.database.close_session(session)


@app.get("/api/analytics")
async def get_analytics(workspaceId: str = None):
    default_data = {
        "totalTasks": 0,
        "completedTasks": 0,
        "completionRate": 0,
        "overdueTasks": 0,
        "totalProjects": 0,
        "totalMembers": 0,
        "workloadDistribution": []
    }
    
    if not bot_instance or not workspaceId:
        return default_data
    
    try:
        stats = bot_instance.database.get_analytics(workspace_id=workspaceId)
        return {**default_data, **stats}
    except Exception as e:
        print(f"Analytics Error: {e}")
        return default_data

@app.post("/api/members/invite")
async def invite_member(invite: MemberInvite):
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    # Get bot info for deep link construction
    try:
        bot_info = await bot_instance.application.bot.get_me()
        bot_username = bot_info.username
    except Exception as e:
        logger.error(f"Failed to get bot info: {e}")
        bot_username = "Bot" # Fallback
        
    deep_link = f"https://t.me/{bot_username}?start=join_{invite.workspaceId}"
    clean_name = invite.username.lstrip('@')
    
    session = bot_instance.database.get_session()
    try:
        from models import User
        from sqlalchemy import func
        # Case-insensitive lookup
        user = session.query(User).filter(func.lower(User.username) == clean_name.lower()).first()
        
        if user:
            try:
                message = (
                    f"🚀 *Workspace Invitation*\n\n"
                    f"You have been invited to the *Nest Command Center* (Workspace: `{invite.workspaceId}`).\n"
                    f"Your assigned tasks will now sync with this dashboard."
                )
                await bot_instance.application.bot.send_message(
                    chat_id=user.telegram_id,
                    text=message,
                    parse_mode="Markdown"
                )
                return {
                    "success": True, 
                    "message": f"Invite sent to @{clean_name} via Telegram DM."
                }
            except Exception as e:
                logger.warning(f"Could not send DM to @{clean_name}: {e}")
                return {
                    "success": True, 
                    "message": f"User exists but I couldn't DM them. Share this link: {deep_link}",
                    "link": deep_link
                }
        else:
            # User not in DB - cannot DM them
            return {
                "success": True, 
                "message": f"@{clean_name} hasn't used the bot yet. Share this invite link with them: {deep_link}",
                "link": deep_link
            }
    finally:
        session.close()

@app.patch("/api/workspaces/{workspace_id}/bot-token")
async def update_workspace_token(workspace_id: str, update: WorkspaceTokenUpdate):
    # Placeholder for actual token storage logic
    logger.info(f"Updating bot token for workspace {workspace_id}")
    return {"success": True, "message": "Bot token updated"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("bot:app", host="0.0.0.0", port=port, reload=True)