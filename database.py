import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from models import SessionLocal, User, Task, TaskAssignment, Reminder, create_tables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        # Create tables
        create_tables()
        logger.info("Database tables created")

    def get_session(self) -> Session:
        return SessionLocal()

    def close_session(self, session: Session):
        session.close()

    def add_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> User:
        session = self.get_session()
        try:
            user = session.get(User, user_id)
            if user:
                # Update existing user
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                session.commit()
            else:
                # Create new user
                user = User(
                    id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                )
                session.add(user)
                session.commit()
                session.refresh(user)
            return user
        finally:
            self.close_session(session)

    def add_task(
        self,
        task_name: str,
        chat_id: int,
        due_date: datetime,
        assigned_user_ids: List[int],
        reminder_minutes_list: Optional[List[int]] = None,
    ) -> dict:
        if reminder_minutes_list is None:
            reminder_minutes_list = [30]

        session = self.get_session()
        try:
            # Create task
            task = Task(
                task_name=task_name,
                chat_id=chat_id,
                due_date=due_date,
            )
            session.add(task)
            session.flush()  # Flush to get the ID without committing

            # Generate task code
            task.task_code = f"TK{task.id:04d}"

            # Add user assignments
            for user_id in assigned_user_ids:
                user = session.get(User, user_id)
                if user:
                    assignment = TaskAssignment(task=task, user=user)
                    session.add(assignment)
                else:
                    logger.warning(f"User {user_id} not found")

            # Create reminders
            for minutes in reminder_minutes_list:
                reminder = Reminder(task=task, minutes_before=minutes)
                session.add(reminder)

            session.commit()  # Single commit for all operations

            # Return task data as dict to avoid detached instance issues
            return {
                "id": task.id,
                "task_code": task.task_code,
                "task_name": task.task_name,
                "chat_id": task.chat_id,
                "due_date": task.due_date,
                "completed": task.completed,
                "created_at": task.created_at,
            }
        finally:
            self.close_session(session)

    def get_user_tasks(self, user_id: int) -> List[dict]:
        session = self.get_session()
        try:
            user = session.get(User, user_id)
            if not user:
                return []

            # Get tasks assigned to this user that are not completed
            tasks = (
                session.query(Task)
                .join(TaskAssignment)
                .filter(TaskAssignment.user_id == user_id, Task.completed == False)
                .all()
            )

            # Convert to dictionaries to avoid detached session issues
            task_data = []
            for task in tasks:
                # Get reminders for this task
                reminders = (
                    session.query(Reminder).filter(Reminder.task_id == task.id).all()
                )
                reminder_data = []
                for reminder in reminders:
                    reminder_data.append(
                        {
                            "id": reminder.id,
                            "minutes_before": reminder.minutes_before,
                            "sent": reminder.sent,
                            "created_at": reminder.created_at,
                        }
                    )

                task_data.append(
                    {
                        "id": task.id,
                        "task_code": task.task_code,
                        "task_name": task.task_name,
                        "chat_id": task.chat_id,
                        "due_date": task.due_date,
                        "completed": task.completed,
                        "created_at": task.created_at,
                        "reminders": reminder_data,
                    }
                )
            return task_data
        finally:
            self.close_session(session)

    def get_pending_reminders(self):
        session = self.get_session()
        try:
            reminders = (
                session.query(Reminder)
                .join(Task)
                .filter(Reminder.sent == False, Task.completed == False)
                .all()
            )

            # Convert to dictionaries to avoid detached session issues
            reminder_data = []
            for reminder in reminders:
                # Get assigned users for this task
                assigned_users = (
                    session.query(User)
                    .join(TaskAssignment)
                    .filter(TaskAssignment.task_id == reminder.task_id)
                    .all()
                )

                user_data = []
                for user in assigned_users:
                    user_data.append(
                        {
                            "id": user.id,
                            "username": user.username,
                            "first_name": user.first_name,
                            "last_name": user.last_name,
                            "receive_reminders": user.receive_reminders,
                        }
                    )

                reminder_data.append(
                    {
                        "id": reminder.id,
                        "task_id": reminder.task_id,
                        "minutes_before": reminder.minutes_before,
                        "sent": reminder.sent,
                        "created_at": reminder.created_at,
                        "task": {
                            "id": reminder.task.id,
                            "task_code": reminder.task.task_code,
                            "task_name": reminder.task.task_name,
                            "chat_id": reminder.task.chat_id,
                            "due_date": reminder.task.due_date,
                            "completed": reminder.task.completed,
                            "created_at": reminder.task.created_at,
                            "assigned_users": user_data,
                        },
                    }
                )
            return reminder_data
        finally:
            self.close_session(session)

    def mark_reminder_sent(self, reminder_id: int) -> bool:
        session = self.get_session()
        try:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                reminder.sent = True
                session.commit()
                return True
            return False
        finally:
            self.close_session(session)

    def get_user(self, user_id: int) -> Optional[User]:
        session = self.get_session()
        try:
            return session.get(User, user_id)
        finally:
            self.close_session(session)

    def update_task_reminders(
        self, task_id: int, reminder_minutes_list: Optional[List[int]] = None
    ) -> bool:
        session = self.get_session()
        try:
            task = session.get(Task, task_id)
            if not task:
                return False

            if reminder_minutes_list is not None:
                # Delete existing reminders
                session.query(Reminder).filter(Reminder.task_id == task_id).delete()

                # Create new reminders
                for minutes in reminder_minutes_list:
                    reminder = Reminder(task=task, minutes_before=minutes)
                    session.add(reminder)

            session.commit()
            return True
        finally:
            self.close_session(session)
