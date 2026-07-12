import sqlite3
from datetime import datetime, timedelta

import desktop_app
from database_schema import ensure_courses_schema


class FixedValue:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def create_legacy_database(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform_name TEXT NOT NULL,
            course_name TEXT NOT NULL,
            course_url TEXT,
            teacher TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            deadline_time TEXT,
            exam_time TEXT,
            status TEXT NOT NULL DEFAULT '未完成',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, platform_name, course_name)
        );
        CREATE TABLE assignment_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform_name TEXT NOT NULL,
            task_type TEXT NOT NULL,
            course_name TEXT NOT NULL,
            task_title TEXT NOT NULL,
            status TEXT NOT NULL,
            external_id TEXT NOT NULL UNIQUE
        );
        INSERT INTO users VALUES (1, 'desktop', 'local', '2026-01-01 00:00:00');
        INSERT INTO courses
            (user_id, platform_name, course_name, status, created_at, updated_at)
        VALUES (1, '学习通', '保留的旧课程', '进行中', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
        INSERT INTO assignment_tasks
            (user_id, platform_name, task_type, course_name, task_title, status, external_id)
        VALUES (1, '学校作业平台', '作业', '保留的旧课程', '保留的任务', '进行中', 'task-1');
        """
    )
    conn.commit()
    return conn


def test_courses_migration_preserves_data_and_allows_same_name(tmp_path):
    conn = create_legacy_database(tmp_path / "legacy.sqlite3")
    ensure_courses_schema(conn)
    conn.execute(
        """
        INSERT INTO courses
            (user_id, platform_name, external_id, course_name, status, created_at, updated_at)
        VALUES (1, '学习通', 'different-course', '保留的旧课程', '进行中', ?, ?)
        """,
        ("2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM assignment_tasks").fetchone()[0] == 1
    assert "external_id" in {row[1] for row in conn.execute("PRAGMA table_info(courses)")}
    conn.close()


def test_desktop_reminders_skip_completed_items(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_app, "DATABASE", str(tmp_path / "reminders.sqlite3"))
    desktop_app.init_db()
    app = desktop_app.MiniReminderApp.__new__(desktop_app.MiniReminderApp)
    app.user_id = desktop_app.get_desktop_user_id()
    app.reminder_days = FixedValue(7)
    deadline = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    now = desktop_app.now_text()

    with desktop_app.connect_db() as conn:
        conn.execute(
            """
            INSERT INTO courses
                (user_id, platform_name, external_id, course_name, deadline_time, status, created_at, updated_at)
            VALUES (?, '学习通', 'done-course', '已完成课程', ?, '已完成', ?, ?)
            """,
            (app.user_id, deadline, now, now),
        )
        conn.execute(
            """
            INSERT INTO assignment_tasks
                (user_id, platform_name, task_type, course_name, task_title, deadline_time,
                 status, external_id, created_at, updated_at)
            VALUES (?, '学校作业平台', '作业', '测试课程', '已完成作业', ?, '已完成', 'done-task', ?, ?)
            """,
            (app.user_id, deadline, now, now),
        )
        conn.commit()

    assert app.build_reminders() == []


def test_ai_learning_data_excludes_terminal_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_app, "DATABASE", str(tmp_path / "ai.sqlite3"))
    desktop_app.init_db()
    app = desktop_app.MiniReminderApp.__new__(desktop_app.MiniReminderApp)
    app.user_id = desktop_app.get_desktop_user_id()
    app.reminder_days = FixedValue(7)
    now = desktop_app.now_text()

    with desktop_app.connect_db() as conn:
        conn.execute(
            """
            INSERT INTO courses
                (user_id, platform_name, external_id, course_name, status, created_at, updated_at)
            VALUES (?, '学习通', 'finished-course', '结束课程', '已结束', ?, ?)
            """,
            (app.user_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO courses
                (user_id, platform_name, external_id, course_name, status, created_at, updated_at)
            VALUES (?, '学习通', 'active-course', '进行中课程', '进行中', ?, ?)
            """,
            (app.user_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO assignment_tasks
                (user_id, platform_name, task_type, course_name, task_title,
                 status, external_id, created_at, updated_at)
            VALUES (?, '学校作业平台', '作业', '测试课程', '完成任务', '已完成', 'finished-task', ?, ?)
            """,
            (app.user_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO assignment_tasks
                (user_id, platform_name, task_type, course_name, task_title,
                 status, external_id, created_at, updated_at)
            VALUES (?, '学校作业平台', '作业', '测试课程', '进行中任务', '进行中', 'active-task', ?, ?)
            """,
            (app.user_id, now, now),
        )
        conn.commit()

    courses, assignments = app.load_ai_learning_data()
    assert [course["course_name"] for course in courses] == ["进行中课程"]
    assert [assignment["task_title"] for assignment in assignments] == ["进行中任务"]
