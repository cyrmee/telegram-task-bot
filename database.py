import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict
from sqlalchemy.orm import Session, joinedload
from telegram import Bot
from sqlalchemy import func
from models import (
    SessionLocal,
    User,
    Task,
    Project,
    TaskAssignment,
    Reminder,
    create_tables,
    migrate_user_table,
    migrate_task_status,
    migrate_projects_table,
    migrate_dashboard_schema,
    TaskStatus,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        # Create tables
        create_tables()
        # Migrate user table if needed
        migrate_user_table()
        # Migrate task status if needed
        migrate_task_status()
        # Migrate projects table if needed
        migrate_projects_table()
        # Migrate dashboard features (Project status, Task description)
        migrate_dashboard_schema()
        logger.info("Database tables created and migrated")
        self.bot = None  # Will be set by the bot instance

    def set_bot(self, bot: Bot):
        """Set the bot instance for fetching user info from Telegram"""
        self.bot = bot

    async def get_user_info_from_telegram(
        self, user_id: int, chat_id: int = None
    ) -> Optional[Dict]:
        """Fetch user information from Telegram API using user ID"""
        if not self.bot:
            logger.warning("Bot instance not set, cannot fetch user info from Telegram")
            return None

        try:
            # Try to get chat member info if chat_id is provided
            if chat_id:
                try:
                    chat_member = await self.bot.get_chat_member(chat_id, user_id)
                    user = chat_member.user
                    return {
                        "telegram_id": user.id,
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                    }
                except Exception as e:
                    logger.debug(
                        f"Could not get user {user_id} from chat {chat_id}: {e}"
                    )

            # Try to get user info directly (this may not always work)
            try:
                chat = await self.bot.get_chat(user_id)
                return {
                    "telegram_id": chat.id,
                    "username": chat.username,
                    "first_name": chat.first_name,
                    "last_name": chat.last_name,
                }
            except Exception as e:
                logger.debug(f"Could not get user {user_id} info directly: {e}")
                return None

        except Exception as e:
            logger.error(f"Error fetching user info from Telegram: {e}")
            return None

    def get_user_by_username(self, username: str) -> Optional[int]:
        """Get user ID by username. Returns telegram_id if found, None otherwise."""
        session = self.get_session()
        try:
            # Remove @ symbol if present
            clean_username = username.lstrip("@")
            from sqlalchemy import func

            user = (
                session.query(User)
                .filter(func.lower(User.username) == clean_username.lower())
                .first()
            )
            return user.telegram_id if user else None
        finally:
            self.close_session(session)

    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        """Get user info by telegram ID. Returns user dict if found, None otherwise."""
        session = self.get_session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                return {
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "receive_reminders": user.receive_reminders,
                }
            return None
        finally:
            self.close_session(session)

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
            # Check if user exists by telegram_id
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                # Update existing user
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                session.commit()
            else:
                # Create new user
                user = User(
                    telegram_id=user_id,
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
        workspace_id: str,
        reminder_minutes_list: Optional[List[int]] = None,
        project_id: Optional[int] = None,
        description: Optional[str] = None,
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
                project_id=project_id,
                workspace_id=workspace_id,
                description=description
            )
            session.add(task)
            session.flush()  # Flush to get the ID without committing

            # Generate task code
            task.task_code = f"TK{task.id:04d}"

            # Add user assignments
            for user_id in assigned_user_ids:
                user = session.query(User).filter_by(telegram_id=user_id).first()
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
                "description": task.description,
                "chat_id": task.chat_id,
                "due_date": task.due_date,
                "status": task.status.value,
                "completed": task.completed,
                "project_id": task.project_id,
                "created_at": task.created_at,
            }
        finally:
            self.close_session(session)

    def get_user_tasks(self, user_id: int) -> List[dict]:
        session = self.get_session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return []

            # Get tasks assigned to this user that are not done
            tasks = (
                session.query(Task)
                .join(TaskAssignment)
                .filter(
                    TaskAssignment.user_id == user.telegram_id,
                    Task.status != TaskStatus.DONE,
                )
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

                # Get assignees for this task
                assignments = (
                    session.query(TaskAssignment)
                    .filter(TaskAssignment.task_id == task.id)
                    .all()
                )
                assignee_data = []
                for assignment in assignments:
                    assignee_user = (
                        session.query(User).filter_by(id=assignment.user_id).first()
                    )
                    if assignee_user:
                        assignee_data.append(
                            {
                                "id": assignee_user.telegram_id,
                                "username": assignee_user.username,
                                "first_name": assignee_user.first_name,
                                "last_name": assignee_user.last_name,
                            }
                        )

                task_data.append(
                    {
                        "id": task.id,
                        "task_code": task.task_code,
                        "task_name": task.task_name,
                        "chat_id": task.chat_id,
                        "due_date": task.due_date,
                        "status": task.status.value,
                        "completed": task.completed,
                        "created_at": task.created_at,
                        "reminders": reminder_data,
                        "assignees": assignee_data,
                    }
                )
            return task_data
        finally:
            self.close_session(session)

    def get_done_tasks_for_user_in_chat(self, user_id: int, chat_id: int) -> List[dict]:
        """Get all done tasks for a user in a specific chat"""
        session = self.get_session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return []

            # Get done tasks assigned to this user in this specific chat
            tasks = (
                session.query(Task)
                .join(TaskAssignment)
                .filter(
                    TaskAssignment.user_id == user.telegram_id,
                    Task.chat_id == chat_id,
                    Task.status == TaskStatus.DONE,
                )
                .order_by(Task.created_at.desc())
                .all()
            )

            # Convert to dictionaries
            task_data = []
            for task in tasks:
                task_data.append(
                    {
                        "id": task.id,
                        "task_code": task.task_code,
                        "task_name": task.task_name,
                        "chat_id": task.chat_id,
                        "due_date": task.due_date,
                        "status": task.status.value,
                        "completed": task.completed,
                        "created_at": task.created_at,
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
                .filter(Reminder.sent == False, Task.status != TaskStatus.DONE)
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
                            "telegram_id": user.telegram_id,
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
                            "status": reminder.task.status.value,
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

    def update_task_status(self, task_id: int, status: TaskStatus) -> bool:
        """Update the status of a task"""
        return self.update_task(task_id, status=status)

    def update_task(self, task_id: int, **kwargs) -> bool:
        """Generic task update method"""
        session = self.get_session()
        try:
            task = session.get(Task, task_id)
            if not task:
                return False

            if "status" in kwargs:
                status = kwargs["status"]
                task.status = status
                task.completed = (status == TaskStatus.DONE)
            
            if "due_date" in kwargs:
                task.due_date = kwargs["due_date"]
            
            if "task_name" in kwargs:
                task.task_name = kwargs["task_name"]
            
            if "project_id" in kwargs:
                task.project_id = kwargs["project_id"]
                
            if "description" in kwargs:
                task.description = kwargs["description"]

            session.commit()
            return True
        finally:
            self.close_session(session)

    def get_task_by_code(self, task_code: str) -> Optional[dict]:
        """Get a task by its task code"""
        session = self.get_session()
        try:
            task = session.query(Task).filter_by(task_code=task_code.upper()).first()
            if not task:
                return None

            return {
                "id": task.id,
                "task_code": task.task_code,
                "task_name": task.task_name,
                "chat_id": task.chat_id,
                "due_date": task.due_date,
                "status": task.status.value,
                "completed": task.completed,
                "created_at": task.created_at,
            }
        finally:
            self.close_session(session)

    def delete_task(self, task_id: int) -> bool:
        """Delete a task and all its related data (assignments and reminders)"""
        session = self.get_session()
        try:
            task = session.get(Task, task_id)
            if not task:
                return False

            # Delete related reminders
            session.query(Reminder).filter(Reminder.task_id == task_id).delete()

            # Delete related task assignments
            session.query(TaskAssignment).filter(
                TaskAssignment.task_id == task_id
            ).delete()

            session.delete(task)

            session.commit()
            return True
        finally:
            self.close_session(session)

    def get_projects(self, workspace_id: Optional[str] = None) -> List[dict]:
        session = self.get_session()
        try:
            query = session.query(Project)
            if workspace_id:
                query = query.filter(Project.workspace_id == workspace_id)
            projects = query.all()
            
            result = []
            for p in projects:
                task_count = session.query(Task).filter(Task.project_id == p.id).count()
                result.append({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                    "workspace_id": p.workspace_id,
                    "created_at": p.created_at,
                    "taskCount": task_count,
                    "tasks": [],
                })
            return result
        finally:
            self.close_session(session)

    def add_project(self, name: str, description: Optional[str] = None, workspace_id: Optional[str] = None) -> dict:
        session = self.get_session()
        try:
            project = Project(name=name, description=description, workspace_id=workspace_id)
            session.add(project)
            session.commit()
            session.refresh(project)
            return {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "workspace_id": project.workspace_id,
                "created_at": project.created_at,
            }
        finally:
            self.close_session(session)

    def delete_project(self, project_id: int) -> bool:
        """Delete a project and unassign it from tasks"""
        session = self.get_session()
        try:
            project = session.get(Project, project_id)
            if not project:
                return False

            # Set project_id to None for all tasks in this project
            session.query(Task).filter(Task.project_id == project_id).update({Task.project_id: None})
            
            session.delete(project)
            session.commit()
            return True
        finally:
            self.close_session(session)

    def update_project(self, project_id: int, **kwargs) -> bool:
        """Update project details"""
        session = self.get_session()
        try:
            project = session.get(Project, project_id)
            if not project:
                return False

            if "name" in kwargs:
                project.name = kwargs["name"]
            if "description" in kwargs:
                project.description = kwargs["description"]
            if "status" in kwargs:
                project.status = kwargs["status"]

            session.commit()
            return True
        finally:
            self.close_session(session)

    def get_analytics(self, workspace_id: str) -> dict:
        session = self.get_session()
        try:
            # Base query for the specific workspace [cite: 111, 128]
            base_query = session.query(Task).filter(Task.workspace_id == workspace_id)
            
            total_tasks = base_query.count()
            completed_tasks = base_query.filter(Task.status == TaskStatus.DONE).count()
            
            now = datetime.now(timezone.utc)
            overdue_tasks = base_query.filter(
                Task.due_date < now,
                Task.status != TaskStatus.DONE
            ).count()

            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

            # Workload Distribution: Join User and TaskAssignments [cite: 129]
            workload_query = session.query(
                User.telegram_id,
                User.username,
                func.count(TaskAssignment.id).label('task_count')
            ).join(TaskAssignment, User.telegram_id == TaskAssignment.user_id)\
            .join(Task, TaskAssignment.task_id == Task.id)\
            .filter(Task.workspace_id == workspace_id, Task.status != TaskStatus.DONE)\
            .group_by(User.telegram_id, User.username).all()

            return {
                "totalTasks": total_tasks,
                "completedTasks": completed_tasks,
                "completionRate": round(completion_rate, 2),
                "overdueTasks": overdue_tasks,
                "totalProjects": session.query(Project).filter(Project.workspace_id == workspace_id).count(),
                "totalMembers": session.query(User).count(),
                "workloadDistribution": [
                    {"memberId": str(w.telegram_id), "username": w.username or f"User_{w.telegram_id}", "taskCount": w.task_count}
                    for w in workload_query
                ]
            }
        finally:
            self.close_session(session)
