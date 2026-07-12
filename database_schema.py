import hashlib
import sqlite3


COURSE_COLUMNS = (
    "id",
    "user_id",
    "platform_name",
    "external_id",
    "course_name",
    "course_url",
    "teacher",
    "progress",
    "deadline_time",
    "exam_time",
    "status",
    "created_at",
    "updated_at",
)


def fallback_course_external_id(platform_name, course_name, course_url=""):
    identity = "\x1f".join((platform_name or "", course_name or "", course_url or ""))
    return "legacy-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def ensure_courses_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(courses)")}
    if not columns:
        _create_courses_table(conn)
        return

    unique_indexes = {
        tuple(index_row[2] for index_row in conn.execute(f"PRAGMA index_info({index[1]})"))
        for index in conn.execute("PRAGMA index_list(courses)")
        if index[2]
    }
    desired_unique = ("user_id", "platform_name", "external_id")
    if "external_id" in columns and desired_unique in unique_indexes:
        return

    try:
        conn.execute("ALTER TABLE courses RENAME TO courses_legacy")
        _create_courses_table(conn)
        cursor = conn.execute("SELECT * FROM courses_legacy ORDER BY id")
        column_names = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
        for row in rows:
            values = dict(zip(column_names, row))
            external_id = values.get("external_id") or fallback_course_external_id(
                values.get("platform_name"), values.get("course_name"), values.get("course_url")
            )
            payload = {
                "id": values.get("id"),
                "user_id": values.get("user_id"),
                "platform_name": values.get("platform_name"),
                "external_id": external_id,
                "course_name": values.get("course_name"),
                "course_url": values.get("course_url"),
                "teacher": values.get("teacher"),
                "progress": values.get("progress") or 0,
                "deadline_time": values.get("deadline_time"),
                "exam_time": values.get("exam_time"),
                "status": values.get("status") or "未完成",
                "created_at": values.get("created_at"),
                "updated_at": values.get("updated_at"),
            }
            conn.execute(
                f"INSERT INTO courses ({', '.join(COURSE_COLUMNS)}) VALUES ({', '.join('?' for _ in COURSE_COLUMNS)})",
                [payload[column] for column in COURSE_COLUMNS],
            )
        conn.execute("DROP TABLE courses_legacy")
    except Exception:
        conn.rollback()
        raise


def _create_courses_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform_name TEXT NOT NULL,
            external_id TEXT NOT NULL,
            course_name TEXT NOT NULL,
            course_url TEXT,
            teacher TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            deadline_time TEXT,
            exam_time TEXT,
            status TEXT NOT NULL DEFAULT '未完成',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, platform_name, external_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
