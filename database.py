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
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import logging

# Create base class for declarative models
Base = declarative_base()

# Association table for many-to-many relationship between tasks and users
task_assignments = Table(
    "task_assignments",
    Base.metadata,
    Column("task_id", Integer, ForeignKey("tasks.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    receive_reminders = Column(Boolean, default=True)  # Opt-in flag
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
    completed = Column(Boolean, default=False)

    # Relationship to users
    assigned_users = relationship(
        "User", secondary=task_assignments, back_populates="tasks"
    )

    # Relationship to reminders
    reminders = relationship(
        "Reminder", back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Task(id={self.id}, name={self.task_name}, due={self.due_date})>"


class Reminder(Base):
    """
    Reminder model to store multiple reminder times for each task.
    """

    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    minutes_before = Column(
        Integer, nullable=False
    )  # Minutes before due date to send reminder
    sent = Column(Boolean, default=False)  # Track if this specific reminder was sent
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship back to task
    task = relationship("Task", back_populates="reminders")

    def __repr__(self):
        return f"<Reminder(id={self.id}, task_id={self.task_id}, minutes_before={self.minutes_before}, sent={self.sent})>"


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
        self.migrate_schema()  # Check and apply any schema migrations
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

    def add_task(
        self,
        task_name,
        chat_id,
        due_date,
        assigned_user_ids,
        reminder_minutes_list=None,
    ):
        """
        Create a new task and assign it to users.

        Args:
            task_name (str): Name/description of the task
            chat_id (int): Group chat ID where task was created
            due_date (datetime): Task deadline
            assigned_user_ids (list): List of user IDs to assign
            reminder_minutes_list (list): List of minutes before due date to send reminders (default [30])

        Returns:
            Task: Created task object
        """
        if reminder_minutes_list is None:
            reminder_minutes_list = [30]  # Default to 30 minutes before

        session = self.get_session()
        try:
            task = Task(task_name=task_name, chat_id=chat_id, due_date=due_date)

            # Assign users to the task
            for user_id in assigned_user_ids:
                user = session.query(User).filter_by(id=user_id).first()
                if user:
                    task.assigned_users.append(user)

            # Create reminders for the task
            for minutes in reminder_minutes_list:
                reminder = Reminder(task=task, minutes_before=minutes)
                session.add(reminder)

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
        Get all reminders that need to be sent.

        Returns:
            list: List of Reminder objects that haven't been sent yet
        """
        session = self.get_session()
        try:
            # Get all reminders that haven't been sent and belong to incomplete tasks
            reminders = (
                session.query(Reminder)
                .join(Task)
                .filter(Reminder.sent == False, Task.completed == False)
                .all()
            )
            return reminders
        finally:
            session.close()

    def mark_reminder_sent(self, reminder_id):
        """
        Mark a specific reminder as sent.

        Args:
            reminder_id (int): Reminder ID
        """
        session = self.get_session()
        try:
            reminder = session.query(Reminder).filter_by(id=reminder_id).first()
            if reminder:
                reminder.sent = True
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

    def update_task_reminders(
        self, task_id, reminder_minutes_list=None, enable_reminders=None
    ):
        """
        Update reminder settings for a specific task.

        Args:
            task_id (int): Task ID
            reminder_minutes_list (list, optional): List of minutes before due date to send reminders
            enable_reminders (bool, optional): Whether to enable reminders for this task (legacy, kept for compatibility)

        Returns:
            bool: True if successful
        """
        session = self.get_session()
        try:
            task = session.query(Task).filter_by(id=task_id).first()
            if task:
                if reminder_minutes_list is not None:
                    # Remove existing reminders
                    session.query(Reminder).filter_by(task_id=task_id).delete()

                    # Add new reminders
                    for minutes in reminder_minutes_list:
                        reminder = Reminder(task_id=task_id, minutes_before=minutes)
                        session.add(reminder)

                # enable_reminders is kept for backward compatibility but doesn't do anything
                # since reminders are now managed per task via the Reminder table

                session.commit()
                return True
            return False
        finally:
            session.close()

    def migrate_schema(self):
        """
        Check and apply any necessary schema migrations for existing databases.
        """
        try:
            # Check if the new columns exist in the tasks table
            with self.engine.connect() as conn:
                # Check for reminder_minutes_before column (old system)
                result = conn.execute(text("PRAGMA table_info(tasks)"))
                columns = [row[1] for row in result.fetchall()]

                # Check if reminders table exists
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'"
                    )
                )
                reminders_table_exists = result.fetchone() is not None

                if not reminders_table_exists:
                    logger.info("Creating reminders table")

                    # Create reminders table
                    conn.execute(
                        text(
                            """
                        CREATE TABLE reminders (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            task_id INTEGER NOT NULL,
                            minutes_before INTEGER NOT NULL,
                            sent BOOLEAN DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (task_id) REFERENCES tasks (id)
                        )
                    """
                        )
                    )

                    # Migrate existing data or create defaults
                    if (
                        "reminder_minutes_before" in columns
                        and "enable_reminders" in columns
                    ):
                        logger.info("Migrating existing reminder data to new table")

                        # Get all tasks with reminder settings
                        result = conn.execute(
                            text(
                                """
                            SELECT id, reminder_minutes_before, enable_reminders
                            FROM tasks
                            WHERE reminder_minutes_before IS NOT NULL
                        """
                            )
                        )

                        for row in result.fetchall():
                            task_id, minutes_before, enable_reminders = row
                            if (
                                enable_reminders
                            ):  # Only migrate if reminders were enabled
                                conn.execute(
                                    text(
                                        f"""
                                    INSERT INTO reminders (task_id, minutes_before, sent)
                                    VALUES ({task_id}, {minutes_before}, 0)
                                """
                                    )
                                )

                        # Remove old columns after migration
                        if "reminder_minutes_before" in columns:
                            logger.info("Removing old reminder_minutes_before column")
                            conn.execute(
                                text(
                                    "ALTER TABLE tasks DROP COLUMN reminder_minutes_before"
                                )
                            )

                        if "enable_reminders" in columns:
                            logger.info("Removing old enable_reminders column")
                            conn.execute(
                                text("ALTER TABLE tasks DROP COLUMN enable_reminders")
                            )

                        if "reminder_sent" in columns:
                            logger.info("Removing old reminder_sent column")
                            conn.execute(
                                text("ALTER TABLE tasks DROP COLUMN reminder_sent")
                            )
                    else:
                        # Create default reminders for all existing tasks
                        logger.info("Creating default reminders for existing tasks")
                        result = conn.execute(text("SELECT id FROM tasks"))
                        for row in result.fetchall():
                            task_id = row[0]
                            # Create default 30-minute reminder for each task
                            conn.execute(
                                text(
                                    f"INSERT INTO reminders (task_id, minutes_before, sent) VALUES ({task_id}, 30, 0)"
                                )
                            )
                            conn.commit()
                else:
                    # Table exists, check if it has data
                    result = conn.execute(text("SELECT COUNT(*) FROM reminders"))
                    reminder_count = result.fetchone()[0]
                    if reminder_count == 0:
                        logger.info(
                            "Reminders table exists but is empty, creating default reminders"
                        )
                        result = conn.execute(text("SELECT id FROM tasks"))
                        for row in result.fetchall():
                            task_id = row[0]
                            # Create default 30-minute reminder for each task
                            conn.execute(
                                text(
                                    f"INSERT INTO reminders (task_id, minutes_before, sent) VALUES ({task_id}, 30, 0)"
                                )
                            )
                        conn.commit()
        except Exception as e:
            logger.warning(f"Schema migration check failed: {e}")
            # Continue anyway - the error might be due to table not existing yet
