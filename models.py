import os

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    BigInteger,
    Enum,
)
import enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func

# Load environment variables
load_dotenv()

# Import database URL from environment
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_URL)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()


# Task status enum
class TaskStatus(enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    receive_reminders = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    task_assignments = relationship("TaskAssignment", back_populates="user")

    def __str__(self):
        return f"User(id={self.id}, telegram_id={self.telegram_id}, username={self.username})"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    task_code = Column(String, unique=True, nullable=True)
    task_name = Column(String, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.NEW, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed = Column(Boolean, default=False)  # Keep for backward compatibility

    # Relationships
    assignments = relationship("TaskAssignment", back_populates="task")
    reminders = relationship("Reminder", back_populates="task")

    def __str__(self):
        return f"Task(id={self.id}, code={self.task_code}, name={self.task_name}, status={self.status.value}, due={self.due_date})"


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)

    # Relationships
    task = relationship("Task", back_populates="assignments")
    user = relationship("User", back_populates="task_assignments")

    __table_args__ = ({"sqlite_autoincrement": True},)  # For SQLite compatibility


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    minutes_before = Column(Integer, nullable=False)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    task = relationship("Task", back_populates="reminders")

    def __str__(self):
        return f"Reminder(id={self.id}, task_id={self.task_id}, minutes_before={self.minutes_before}, sent={self.sent})"


# Create tables function
def create_tables():
    Base.metadata.create_all(bind=engine)


def migrate_user_table():
    """Migrate existing user table to add telegram_id column"""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # Check if telegram_id column exists
            result = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='telegram_id'"
                )
            )
            if not result.fetchone():
                print("Migrating user table...")
                # Add telegram_id column
                conn.execute(text("ALTER TABLE users ADD COLUMN telegram_id BIGINT"))
                # Copy id values to telegram_id
                conn.execute(text("UPDATE users SET telegram_id = id"))
                # Add unique constraint
                conn.execute(
                    text(
                        "ALTER TABLE users ADD CONSTRAINT users_telegram_id_key UNIQUE (telegram_id)"
                    )
                )
                # Make telegram_id NOT NULL
                conn.execute(
                    text("ALTER TABLE users ALTER COLUMN telegram_id SET NOT NULL")
                )

                # Update foreign key reference (this might fail if there are existing constraints)
                try:
                    conn.execute(
                        text(
                            "ALTER TABLE task_assignments DROP CONSTRAINT IF EXISTS task_assignments_user_id_fkey"
                        )
                    )
                    conn.execute(
                        text(
                            "ALTER TABLE task_assignments ADD CONSTRAINT task_assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(telegram_id)"
                        )
                    )
                except Exception as e:
                    print(f"Foreign key migration failed: {e}")

                conn.commit()
                print("User table migrated successfully")
            else:
                print("telegram_id column already exists")
    except Exception as e:
        print(f"Migration failed: {e}")
        # Continue anyway - the application might still work


def migrate_task_status():
    """Migrate existing task table to add status column"""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # Check if status column exists
            result = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='status'"
                )
            )
            if not result.fetchone():
                print("Migrating task table to add status column...")
                # Add status column with default value 'NEW' (uppercase)
                conn.execute(
                    text(
                        "ALTER TABLE tasks ADD COLUMN status VARCHAR(20) DEFAULT 'NEW' NOT NULL"
                    )
                )
                # Update existing tasks: if completed=true, set to 'DONE', else 'NEW'
                conn.execute(
                    text(
                        "UPDATE tasks SET status = CASE WHEN completed = true THEN 'DONE' ELSE 'NEW' END"
                    )
                )
                conn.commit()
                print("Task status column added successfully")
            else:
                print("Status column already exists")
                # Fix existing lowercase values
                try:
                    conn.execute(
                        text(
                            "UPDATE tasks SET status = UPPER(status) WHERE status IN ('new', 'in_progress', 'done')"
                        )
                    )
                    conn.commit()
                    print("Fixed status column values to uppercase")
                except Exception as e:
                    print(f"Note: Could not update status values: {e}")
    except Exception as e:
        print(f"Task status migration failed: {e}")
        # Continue anyway - the application might still work
