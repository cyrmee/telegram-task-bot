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

# Fix for psycopg2 not accepting ?schema= parameter in DSN
connect_args = {}
if "?schema=" in DATABASE_URL:
    base_url, schema_part = DATABASE_URL.split("?schema=")
    DATABASE_URL = base_url
    connect_args = {"options": f"-c search_path={schema_part}"}

# Create SQLAlchemy engine
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()


# Task status enum
class TaskStatus(enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    workspace_id = Column(String, nullable=True) 

    # Relationships
    tasks = relationship("Task", back_populates="project")

    def __str__(self):
        return f"Project(id={self.id}, name={self.name})"


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
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)

    workspace_id = Column(String, nullable=True)

    # Relationships
    assignments = relationship("TaskAssignment", back_populates="task")
    reminders = relationship("Reminder", back_populates="task")
    project = relationship("Project", back_populates="tasks")

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
    """Migrate existing task table to add status column and fix values"""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # Check if status column exists
            result = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='status'")
            )
            
            if not result.fetchone():
                print("Adding status column...")
                # We add it as a string first to make migration easier, then we'll let SQLAlchemy handle the Enum
                conn.execute(text("ALTER TABLE tasks ADD COLUMN status VARCHAR(20) DEFAULT 'NEW'"))
                conn.execute(text("UPDATE tasks SET status = 'DONE' WHERE completed = true"))
                conn.commit()
            else:
                print("Status column already exists. Skipping raw string migration.")
                
            # NEW: Add workspace_id migration here too
            ws_check = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='workspace_id'")
            )
            if not ws_check.fetchone():
                print("Adding workspace_id column...")
                conn.execute(text("ALTER TABLE tasks ADD COLUMN workspace_id VARCHAR(100)"))
                conn.commit()
                
    except Exception as e:
        print(f"Migration error: {e}")

def migrate_projects_table():
    """Migrate existing projects table to add workspace_id column"""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # Check if workspace_id column exists in projects table
            result = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='workspace_id'")
            )
            if not result.fetchone():
                print("Migrating projects table to add workspace_id column...")
                conn.execute(text("ALTER TABLE projects ADD COLUMN workspace_id VARCHAR(100)"))
                conn.commit()
                print("Project workspace_id column added successfully")
            else:
                print("workspace_id column already exists in projects")
    except Exception as e:
        print(f"Project migration failed: {e}")
