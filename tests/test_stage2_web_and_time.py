import app as web_app
from time_utils import parse_datetime


def test_common_time_parser_supports_three_formats():
    assert parse_datetime("2026-07-12").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 00:00:00"
    assert parse_datetime("2026-07-12 08:30").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 08:30:00"
    assert parse_datetime("2026-07-12 08:30:45").strftime("%Y-%m-%d %H:%M:%S") == "2026-07-12 08:30:45"


def test_date_short_returns_fallback_for_invalid_value():
    assert web_app.date_short("not-a-date") == "暂无"
    assert web_app.date_short(None) == "暂无"


def test_web_database_enables_foreign_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATABASE", str(tmp_path / "web.sqlite3"))
    with web_app.app.app_context():
        assert web_app.get_db().execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fetched_courses_are_stored_server_side(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATABASE", str(tmp_path / "web-cache.sqlite3"))
    web_app.app.config.update(TESTING=True, SECRET_KEY="test-only-secret")
    now = web_app.now_text()
    with web_app.app.app_context():
        web_app.init_db()
        db = web_app.get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES ('tester', 'hash', ?)",
            (now,),
        ).lastrowid
        db.execute(
            """
            INSERT INTO platform_accounts
                (user_id, platform_name, status, auth_cookie, api_url, created_at, updated_at)
            VALUES (?, '学习通', '已绑定', 'test-cookie', 'https://example.com/courses', ?, ?)
            """,
            (user_id, now, now),
        )
        db.commit()

    monkeypatch.setattr(
        web_app,
        "crawl_platform_courses",
        lambda _platform, _account: [
            {
                "external_id": "course-1",
                "course_name": "测试课程",
                "course_url": "https://example.com/course/1",
                "teacher": "教师",
                "progress": 0,
                "deadline_time": None,
                "exam_time": None,
                "status": "进行中",
            }
        ],
    )
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id

    response = client.post("/fetch/学习通")
    assert response.status_code == 200
    with client.session_transaction() as session:
        assert all(not key.startswith("fetched_courses:") for key in session)
    with web_app.app.app_context():
        cached = web_app.get_db().execute("SELECT COUNT(*) FROM fetched_course_cache").fetchone()[0]
        assert cached == 1
