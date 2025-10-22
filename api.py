from contextlib import asynccontextmanager
from typing import List, Optional
from datetime import datetime
import logging
import os

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from telegram import Update

from database import SessionLocal
from models import User, Task, TaskStatus, TaskAssignment
from bot import TaskBot


logger = logging.getLogger(__name__)


# Constants
USER_NOT_FOUND = "User not found"
TASK_NOT_FOUND = "Task not found"


# Pydantic schemas
class UserBase(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    receive_reminders: bool = True


class UserCreate(UserBase):
    pass


class UserResponse(UserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TaskBase(BaseModel):
    task_name: str
    chat_id: int
    due_date: datetime
    status: TaskStatus = TaskStatus.NEW


class TaskCreate(TaskBase):
    pass


class TaskResponse(TaskBase):
    id: int
    task_code: Optional[str] = None
    created_at: datetime
    completed: bool

    class Config:
        from_attributes = True


class TaskAssignmentResponse(BaseModel):
    id: int
    task_id: int
    user_id: int

    class Config:
        from_attributes = True


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting FastAPI application...")
    # Initialize bot for webhook processing
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        from bot import TaskBot

        app.bot_instance = TaskBot(token)
        await app.bot_instance.application.initialize()
        await app.bot_instance.post_init(app.bot_instance.application)
        print("Bot initialized for webhook processing")
    else:
        print("No bot token found, webhook will not work")
    yield
    # Shutdown
    print("Shutting down FastAPI application...")
    if hasattr(app, "bot_instance") and app.bot_instance.scheduler:
        app.bot_instance.scheduler.shutdown()


# Create FastAPI app
app = FastAPI(
    title="Telegram Task Bot API",
    description="REST API for managing tasks and users",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Routes
@app.get("/")
async def root():
    return {"message": "Telegram Task Bot API"}


@app.post("/users/", response_model=UserResponse)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(User).filter(User.telegram_id == user.telegram_id).first()
    if db_user:
        raise HTTPException(status_code=400, detail="User already exists")

    db_user = User(**user.model_dump())
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@app.get("/users/{telegram_id}", response_model=UserResponse)
async def get_user(telegram_id: int, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    return db_user


@app.get("/users/", response_model=List[UserResponse])
async def get_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    users = db.query(User).offset(skip).limit(limit).all()
    return users


@app.post("/tasks/", response_model=TaskResponse)
async def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    db_task = Task(**task.model_dump())
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get("/tasks/", response_model=List[TaskResponse])
async def get_tasks(
    skip: int = 0,
    limit: int = 100,
    status: Optional[TaskStatus] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status)
    tasks = query.offset(skip).limit(limit).all()
    return tasks


@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)
    return db_task


@app.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int, task_update: TaskBase, db: Session = Depends(get_db)
):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)

    for key, value in task_update.model_dump().items():
        setattr(db_task, key, value)

    db.commit()
    db.refresh(db_task)
    return db_task


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)

    db.delete(db_task)
    db.commit()
    return {"message": "Task deleted successfully"}


@app.post("/tasks/{task_id}/assign/{telegram_id}")
async def assign_task_to_user(
    task_id: int, telegram_id: int, db: Session = Depends(get_db)
):
    # Check if task exists
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)

    # Check if user exists
    db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)

    # Check if assignment already exists
    existing = (
        db.query(TaskAssignment)
        .filter(
            TaskAssignment.task_id == task_id, TaskAssignment.user_id == telegram_id
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Task already assigned to user")

    assignment = TaskAssignment(task_id=task_id, user_id=telegram_id)
    db.add(assignment)
    db.commit()
    return {"message": "Task assigned successfully"}


@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    # Verify token for security (optional but recommended)
    expected_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if expected_token and token != expected_token.split(":")[0]:  # Use bot id as token
        raise HTTPException(status_code=403, detail="Invalid token")

    if not hasattr(app, "bot_instance"):
        raise HTTPException(status_code=500, detail="Bot not initialized")

    try:
        # Get the update data
        data = await request.json()
        update = Update.de_json(data, app.bot_instance.application.bot)

        # Process the update
        await app.bot_instance.application.process_update(update)

        return {"ok": True}

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
