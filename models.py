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
)
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


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    receive_reminders = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    task_assignments = relationship("TaskAssignment", back_populates="user")

    def __str__(self):
        return f"User(id={self.id}, username={self.username})"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    task_code = Column(String, unique=True, nullable=True)
    task_name = Column(String, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed = Column(Boolean, default=False)

    # Relationships
    assignments = relationship("TaskAssignment", back_populates="task")
    reminders = relationship("Reminder", back_populates="task")

    def __str__(self):
        return f"Task(id={self.id}, code={self.task_code}, name={self.task_name}, due={self.due_date})"


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True)
    task_id = Column(BigInteger, ForeignKey("tasks.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

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
