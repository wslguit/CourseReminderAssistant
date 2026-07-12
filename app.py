from datetime import datetime
import os
import sqlite3
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database_schema import ensure_courses_schema, fallback_course_external_id

from platform_api import (
    ScraperError,
    crawl_course_details,
    crawl_platform_courses,
    platform_login_url,
    supported_platforms,
)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "data.sqlite3")


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS platform_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform_name TEXT NOT NULL,
            platform_username TEXT,
            platform_password TEXT,
            status TEXT NOT NULL DEFAULT '未绑定',
            last_sync_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, platform_name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        """
    )
    ensure_courses_schema(db)
    ensure_column("platform_accounts", "platform_username", "TEXT")
    ensure_column("platform_accounts", "platform_password", "TEXT")
    ensure_column("platform_accounts", "last_sync_at", "TEXT")
    ensure_column("platform_accounts", "auth_cookie", "TEXT")
    ensure_column("platform_accounts", "api_url", "TEXT")
    ensure_column("platform_accounts", "referer", "TEXT")
    ensure_column("platform_accounts", "user_agent", "TEXT")
    ensure_column("platform_accounts", "api_method", "TEXT")
    ensure_column("platform_accounts", "api_body", "TEXT")
    db.commit()


def ensure_column(table, column, column_type):
    db = get_db()
    columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


@app.before_request
def load_logged_in_user():
    init_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def course_status(progress):
    if progress >= 100:
        return "已完成"
    if progress >= 60:
        return "进行中"
    return "未完成"


def get_courses(user_id, platform=None, status=None):
    query = "SELECT * FROM courses WHERE user_id = ?"
    params = [user_id]
    if platform and platform != "全部平台":
        query += " AND platform_name = ?"
        params.append(platform)
    if status and status != "全部状态":
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY COALESCE(deadline_time, exam_time, updated_at) ASC"
    return get_db().execute(query, params).fetchall()


def build_reminders(courses):
    reminders = []
    now = datetime.now()
    for course in courses:
        if course["status"] in {"已完成", "已结束"}:
            continue
        deadline = parse_dt(course["deadline_time"])
        exam = parse_dt(course["exam_time"])
        if deadline:
            hours = (deadline - now).total_seconds() / 3600
            if hours < 0 and course["status"] not in {"已完成", "已结束"}:
                reminders.append(
                    {
                        "level": "danger",
                        "title": "课程已逾期",
                        "course_name": course["course_name"],
                        "platform": course["platform_name"],
                        "time": deadline.strftime("%m-%d %H:%M"),
                        "course_id": course["id"],
                    }
                )
            elif 0 <= hours <= 72:
                reminders.append(
                    {
                        "level": "danger" if hours <= 24 else "warning",
                        "title": "课程截止提醒",
                        "course_name": course["course_name"],
                        "platform": course["platform_name"],
                        "time": deadline.strftime("%m-%d %H:%M"),
                        "course_id": course["id"],
                    }
                )
        if exam:
            hours = (exam - now).total_seconds() / 3600
            if 0 <= hours <= 168:
                reminders.append(
                    {
                        "level": "danger" if hours <= 48 else "warning",
                        "title": "考试提醒",
                        "course_name": course["course_name"],
                        "platform": course["platform_name"],
                        "time": exam.strftime("%m-%d %H:%M"),
                        "course_id": course["id"],
                    }
                )
        if course["progress"] < 50 and course["status"] not in {"已完成", "已结束"}:
            reminders.append(
                {
                    "level": "muted",
                    "title": "进度偏低",
                    "course_name": course["course_name"],
                    "platform": course["platform_name"],
                    "time": f"{course['progress']}%",
                    "course_id": course["id"],
                }
            )
    level_order = {"danger": 0, "warning": 1, "muted": 2}
    return sorted(reminders, key=lambda item: (level_order.get(item["level"], 9), item["time"]))


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("请输入用户名和密码。", "error")
        elif len(password) < 6:
            flash("密码至少需要 6 位。", "error")
        else:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), now_text()),
                )
                db.commit()
                flash("注册成功，请登录。", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("这个用户名已经被注册。", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码不正确。", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    courses = get_courses(g.user["id"])
    reminders = build_reminders(courses)
    total = len(courses)
    finished = len([item for item in courses if item["status"] == "已完成"])
    unfinished = len([item for item in courses if item["status"] != "已完成"])
    avg_progress = round(sum(item["progress"] for item in courses) / total) if total else 0
    return render_template(
        "dashboard.html",
        courses=courses[:6],
        reminders=reminders[:8],
        stats={
            "total": total,
            "finished": finished,
            "unfinished": unfinished,
            "avg_progress": avg_progress,
        },
    )


@app.route("/reminders")
@login_required
def reminders():
    courses = get_courses(g.user["id"])
    reminders = build_reminders(courses)
    return render_template(
        "reminders.html",
        reminders=reminders,
        stats={
            "total": len(reminders),
            "danger": len([item for item in reminders if item["level"] == "danger"]),
            "warning": len([item for item in reminders if item["level"] == "warning"]),
            "muted": len([item for item in reminders if item["level"] == "muted"]),
        },
    )


@app.route("/courses")
@login_required
def courses():
    platform = request.args.get("platform", "全部平台")
    status = request.args.get("status", "全部状态")
    rows = get_courses(g.user["id"], platform, status)
    platforms = [
        row["platform_name"]
        for row in get_db()
        .execute(
            "SELECT DISTINCT platform_name FROM courses WHERE user_id = ? ORDER BY platform_name",
            (g.user["id"],),
        )
        .fetchall()
    ]
    return render_template(
        "courses.html",
        courses=rows,
        platforms=platforms,
        selected_platform=platform,
        selected_status=status,
    )


@app.route("/courses/delete", methods=("POST",))
@login_required
def delete_courses():
    course_ids = request.form.getlist("course_id")
    if not course_ids:
        flash("请先勾选要清除的课程。", "error")
        return redirect(url_for("courses"))

    placeholders = ",".join("?" for _ in course_ids)
    params = [g.user["id"], *course_ids]
    cursor = get_db().execute(
        f"DELETE FROM courses WHERE user_id = ? AND id IN ({placeholders})",
        params,
    )
    get_db().commit()
    flash(f"已清除 {cursor.rowcount} 门课程。", "success")
    return redirect(url_for("courses"))


@app.route("/platforms")
@login_required
def platforms():
    supported = supported_platforms()
    rows = get_db().execute(
        "SELECT * FROM platform_accounts WHERE user_id = ? ORDER BY platform_name",
        (g.user["id"],),
    ).fetchall()
    by_name = {row["platform_name"]: row for row in rows}
    return render_template("platforms.html", supported=supported, accounts=by_name)


@app.route("/bind/<platform_name>", methods=("GET", "POST"))
@login_required
def bind_platform(platform_name):
    account = get_db().execute(
        "SELECT * FROM platform_accounts WHERE user_id = ? AND platform_name = ?",
        (g.user["id"], platform_name),
    ).fetchone()
    if request.method == "GET":
        return render_template(
            "platform_login.html",
            platform_name=platform_name,
            account=account,
            login_url=platform_login_url(platform_name),
        )

    api_url = request.form.get("api_url", "").strip()
    auth_cookie = request.form.get("auth_cookie", "").strip()
    referer = request.form.get("referer", "").strip()
    user_agent = request.form.get("user_agent", "").strip()
    api_method = request.form.get("api_method", "GET").strip().upper()
    api_body = request.form.get("api_body", "").strip()
    if api_method not in {"GET", "POST"}:
        api_method = "GET"
    if not api_url or not auth_cookie:
        flash("请填写课程接口 URL 和 Cookie。", "error")
        return redirect(url_for("bind_platform", platform_name=platform_name))

    db = get_db()
    db.execute(
        """
        INSERT INTO platform_accounts
            (user_id, platform_name, status, auth_cookie, api_url, referer, user_agent, api_method, api_body, created_at, updated_at)
        VALUES (?, ?, '已绑定接口', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, platform_name)
        DO UPDATE SET
            status = '已绑定接口',
            auth_cookie = excluded.auth_cookie,
            api_url = excluded.api_url,
            referer = excluded.referer,
            user_agent = excluded.user_agent,
            api_method = excluded.api_method,
            api_body = excluded.api_body,
            updated_at = excluded.updated_at
        """,
        (
            g.user["id"],
            platform_name,
            auth_cookie,
            api_url,
            referer,
            user_agent,
            api_method,
            api_body,
            now_text(),
            now_text(),
        ),
    )
    db.commit()
    flash(f"{platform_name} 接口授权已保存，现在可以读取课程。", "success")
    return redirect(url_for("platforms"))


@app.route("/fetch/<platform_name>", methods=("POST",))
@login_required
def fetch_courses(platform_name):
    account = get_db().execute(
        "SELECT * FROM platform_accounts WHERE user_id = ? AND platform_name = ?",
        (g.user["id"], platform_name),
    ).fetchone()
    if account is None or account["status"] == "未绑定":
        flash("请先绑定该平台的课程接口和 Cookie。", "error")
        return redirect(url_for("bind_platform", platform_name=platform_name))

    try:
        fetched = crawl_platform_courses(platform_name, account)
    except ScraperError as exc:
        flash(str(exc), "error")
        return redirect(url_for("platforms"))
    except Exception as exc:
        flash(f"读取课程失败：{exc}", "error")
        return redirect(url_for("platforms"))

    if not fetched:
        flash("没有识别到课程。请确认填写的是课程列表接口，并且 Cookie 仍然有效。", "error")
        return redirect(url_for("platforms"))

    session[f"fetched_courses:{platform_name}"] = fetched
    get_db().execute(
        """
        UPDATE platform_accounts
        SET status = '已获取课程', updated_at = ?
        WHERE user_id = ? AND platform_name = ?
        """,
        (now_text(), g.user["id"], platform_name),
    )
    get_db().commit()
    return render_template(
        "select_courses.html",
        platform_name=platform_name,
        account=account,
        fetched_courses=fetched,
    )


@app.route("/sync/<platform_name>", methods=("POST",))
@login_required
def sync_platform(platform_name):
    fetched = session.get(f"fetched_courses:{platform_name}", [])
    selected_ids = set(request.form.getlist("course_id"))
    if not fetched:
        flash("请先获取课程列表。", "error")
        return redirect(url_for("platforms"))
    if not selected_ids:
        flash("请至少选择一门课程。", "error")
        return render_template(
            "select_courses.html",
            platform_name=platform_name,
            fetched_courses=fetched,
            account=None,
        )

    db = get_db()
    imported_count = 0
    selected_courses = [item for item in fetched if item["external_id"] in selected_ids]
    try:
        account = get_db().execute(
            "SELECT * FROM platform_accounts WHERE user_id = ? AND platform_name = ?",
            (g.user["id"], platform_name),
        ).fetchone()
        selected_courses = crawl_course_details(platform_name, account, selected_courses)
    except ScraperError as exc:
        flash(str(exc), "error")
        return redirect(url_for("platforms"))
    except Exception as exc:
        flash(f"课程详情读取失败，已导入课程列表基础信息：{exc}", "error")

    for item in selected_courses:
        db.execute(
            """
            INSERT INTO courses
                (user_id, platform_name, external_id, course_name, course_url, teacher, progress,
                 deadline_time, exam_time, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, platform_name, external_id)
            DO UPDATE SET
                course_name = excluded.course_name,
                course_url = excluded.course_url,
                teacher = excluded.teacher,
                progress = excluded.progress,
                deadline_time = excluded.deadline_time,
                exam_time = excluded.exam_time,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                g.user["id"],
                platform_name,
                item.get("external_id") or fallback_course_external_id(
                    platform_name, item.get("course_name"), item.get("course_url")
                ),
                item["course_name"],
                item["course_url"],
                item["teacher"],
                item.get("progress", 0),
                item["deadline_time"],
                item["exam_time"],
                item.get("status") or course_status(item.get("progress", 0)),
                now_text(),
                now_text(),
            ),
        )
        imported_count += 1
    db.execute(
        """
        UPDATE platform_accounts
        SET status = '已同步', last_sync_at = ?, updated_at = ?
        WHERE user_id = ? AND platform_name = ?
        """,
        (now_text(), now_text(), g.user["id"], platform_name),
    )
    db.commit()
    session.pop(f"fetched_courses:{platform_name}", None)
    flash(f"已从 {platform_name} 导入 {imported_count} 门课程。", "success")
    return redirect(url_for("courses", platform=platform_name))


@app.route("/course/<int:course_id>/delete", methods=("POST",))
@login_required
def delete_course(course_id):
    cursor = get_db().execute(
        "DELETE FROM courses WHERE id = ? AND user_id = ?",
        (course_id, g.user["id"]),
    )
    get_db().commit()
    if cursor.rowcount:
        flash("课程已清除。", "success")
    else:
        flash("课程不存在或没有权限。", "error")
    return redirect(url_for("courses"))


@app.route("/course/<int:course_id>")
@login_required
def course_detail(course_id):
    course = get_db().execute(
        "SELECT * FROM courses WHERE id = ? AND user_id = ?", (course_id, g.user["id"])
    ).fetchone()
    if course is None:
        flash("课程不存在。", "error")
        return redirect(url_for("courses"))
    return render_template("course_detail.html", course=course)


@app.template_filter("date_short")
def date_short(value):
    if not value:
        return "暂无"
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%m-%d %H:%M")


if __name__ == "__main__":
    app.run(debug=True)
