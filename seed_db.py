import os
from datetime import datetime, timedelta, timezone
from models import SessionLocal, User, Project, Task, TaskAssignment, TaskStatus, Workspace, Member
from dotenv import load_dotenv

load_dotenv()

def seed_data():
    session = SessionLocal()
    
    try:
        # 1. Create a Workspace [cite: 17]
        ws_id = "ws_01"
        ws = Workspace(id=ws_id, name="Nest HQ")
        session.add(ws)
        session.flush()

        # 2. Create Projects [cite: 117]
        p1 = Project(name="Q1 Product Launch", description="Main campaign", workspace_id=ws_id)
        p2 = Project(name="Internal Tools", description="Bot maintenance", workspace_id=ws_id)
        session.add_all([p1, p2])
        session.flush()

        # 3. Create Users
        u1 = User(telegram_id=12345678, username="sara_dev", first_name="Sara")
        u2 = User(telegram_id=87654321, username="v_choom", first_name="V")
        session.add_all([u1, u2])
        session.flush()

        # 4. Create Members (Links User to Workspace for Dashboard visibility)
        m1 = Member(id="mem_01", telegram_id=u1.telegram_id, username=u1.username, workspace_id=ws_id)
        m2 = Member(id="mem_02", telegram_id=u2.telegram_id, username=u2.username, workspace_id=ws_id)
        session.add_all([m1, m2])
        session.flush()

        # Task 1: Completed
        t1 = Task(
            task_name="Finalize API Docs",
            chat_id=u1.telegram_id,
            due_date=datetime.now(timezone.utc) - timedelta(days=1),
            status=TaskStatus.DONE,
            completed=True,
            project_id=p1.id,
            workspace_id=ws_id,
            assignee_id=m1.id
        )
        
        # Task 2: Pending
        t2 = Task(
            task_name="Integrate Webhooks",
            chat_id=u1.telegram_id,
            due_date=datetime.now(timezone.utc) + timedelta(days=2),
            status=TaskStatus.NEW,
            project_id=p2.id,
            workspace_id=ws_id,
            assignee_id=m1.id
        )
 
        # Task 3: Overdue 
        t3 = Task(
            task_name="Fix Enum Crash",
            chat_id=u2.telegram_id,
            due_date=datetime.now(timezone.utc) - timedelta(hours=5),
            status=TaskStatus.IN_PROGRESS,
            project_id=p2.id,
            workspace_id=ws_id,
            assignee_id=m2.id
        )

        session.add_all([t1, t2, t3])
        session.flush()

        # 5. Create Assignments
        session.add(TaskAssignment(task_id=t1.id, user_id=u1.telegram_id))
        session.add(TaskAssignment(task_id=t2.id, user_id=u1.telegram_id))
        session.add(TaskAssignment(task_id=t3.id, user_id=u2.telegram_id))

        session.commit()
        print("✅ Database successfully populated. Nest Command is ready for action.")

    except Exception as e:
        session.rollback()
        print(f"❌ System Failure during seeding: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_data()