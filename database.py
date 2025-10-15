"""
Database models and connection management for the Telegram Task Bot.
Uses SQLAlchemy ORM with SQLite backend.
"""

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Table,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

# Create base class for declarative models
Base = declarative_base()

# Association table for many-to-many relationship between tasks and users
task_assignments = Table(
    "task_assignments",
    Base.metadata,
    Column("task_id", Integer, ForeignKey("tasks.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)


class User(Base):
    """
    User model to store Telegram user information.
    Tracks whether user has opted in to receive reminders.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)  # Telegram user ID
    username = Column(String, nullable=True)  # Telegram username
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    receive_reminders = Column(Boolean, default=False)  # Opt-in flag
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to tasks
    tasks = relationship(
        "Task", secondary=task_assignments, back_populates="assigned_users"
    )

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username})>"


class Task(Base):
    """
    Task model to store group tasks with assignments and deadlines.
    """

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String, nullable=False)
    chat_id = Column(Integer, nullable=False)  # Group chat ID
    due_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    reminder_sent = Column(Boolean, default=False)  # Track if reminder was sent
    completed = Column(Boolean, default=False)

    # Relationship to users
    assigned_users = relationship(
        "User", secondary=task_assignments, back_populates="tasks"
    )

    def __repr__(self):
        return f"<Task(id={self.id}, name={self.task_name}, due={self.due_date})>"


class Database:
    """
    Database connection manager with session handling.
    """

    def __init__(self, db_url="sqlite:///telegram_tasks.db"):
        """
        Initialize database connection and create tables.

        Args:
            db_url (str): SQLAlchemy database URL
        """
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        """
        Create and return a new database session.

        Returns:
            Session: SQLAlchemy session object
        """
        return self.Session()

    def add_user(self, user_id, username=None, first_name=None, last_name=None):
        """
        Add or update a user in the database.

        Args:
            user_id (int): Telegram user ID
            username (str): Telegram username
            first_name (str): User's first name
            last_name (str): User's last name

        Returns:
            User: User object
        """
        session = self.get_session()
        try:
            user = session.query(User).filter_by(id=user_id).first()
            if not user:
                user = User(
                    id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                )
                session.add(user)
            else:
                # Update user info if changed
                user.username = username
                user.first_name = first_name
                user.last_name = last_name

            session.commit()
            return user
        finally:
            session.close()

    def enable_reminders(self, user_id):
        """
        Enable reminders for a specific user (opt-in).

        Args:
            user_id (int): Telegram user ID

        Returns:
            bool: True if successful
        """
        session = self.get_session()
        try:
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                user.receive_reminders = True
                session.commit()
                return True
            return False
        finally:
            session.close()

    def add_task(self, task_name, chat_id, due_date, assigned_user_ids):
        """
        Create a new task and assign it to users.

        Args:
            task_name (str): Name/description of the task
            chat_id (int): Group chat ID where task was created
            due_date (datetime): Task deadline
            assigned_user_ids (list): List of user IDs to assign

        Returns:
            Task: Created task object
        """
        session = self.get_session()
        try:
            task = Task(task_name=task_name, chat_id=chat_id, due_date=due_date)

            # Assign users to the task
            for user_id in assigned_user_ids:
                user = session.query(User).filter_by(id=user_id).first()
                if user:
                    task.assigned_users.append(user)

            session.add(task)
            session.commit()
            session.refresh(task)
            return task
        finally:
            session.close()

    def get_user_tasks(self, user_id):
        """
        Retrieve all active tasks assigned to a user.

        Args:
            user_id (int): Telegram user ID

        Returns:
            list: List of Task objects
        """
        session = self.get_session()
        try:
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                # Return only incomplete tasks
                return [task for task in user.tasks if not task.completed]
            return []
        finally:
            session.close()

    def get_pending_reminders(self):
        """
        Get all tasks that need reminders sent.

        Returns:
            list: List of Task objects that haven't had reminders sent
        """
        session = self.get_session()
        try:
            tasks = (
                session.query(Task)
                .filter_by(reminder_sent=False, completed=False)
                .all()
            )
            return tasks
        finally:
            session.close()

    def mark_reminder_sent(self, task_id):
        """
        Mark a task's reminder as sent.

        Args:
            task_id (int): Task ID
        """
        session = self.get_session()
        try:
            task = session.query(Task).filter_by(id=task_id).first()
            if task:
                task.reminder_sent = True
                session.commit()
        finally:
            session.close()

    def get_user(self, user_id):
        """
        Retrieve a user by ID.

        Args:
            user_id (int): Telegram user ID

        Returns:
            User: User object or None
        """
        session = self.get_session()
        try:
            return session.query(User).filter_by(id=user_id).first()
        finally:
            session.close()
