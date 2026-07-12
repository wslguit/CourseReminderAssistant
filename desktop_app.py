import os
import sys
import sqlite3
import threading
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

from ai_agent import INTRO_MESSAGE, LLMError, LearningAgent
from app_info import APP_NAME, APP_VERSION
from app_paths import database_path
from credential_store import ai_key, cookie_key, delete_secret, get_secret, set_secret
from database_schema import ensure_courses_schema, fallback_course_external_id
from platform_api import (
    ScraperError,
    crawl_assignment_tasks,
    crawl_platform_courses,
    platform_login_url,
    supported_assignment_platforms,
    supported_platforms,
)
from time_utils import parse_datetime
from security_utils import validate_api_url


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = database_path()


def resource_path(relative_path):
    root = getattr(__import__("sys"), "_MEIPASS", BASE_DIR)
    return os.path.join(root, relative_path)


def gif_frame_count(path):
    try:
        with open(path, "rb") as gif_file:
            data = gif_file.read()
    except OSError:
        return 0
    if len(data) < 13 or data[:3] != b"GIF":
        return 0

    offset = 13
    packed = data[10]
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))

    frames = 0
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            break
        if marker == 0x21:
            if offset >= len(data):
                break
            offset += 1
            while offset < len(data):
                block_size = data[offset]
                offset += 1
                if block_size == 0:
                    break
                offset += block_size
            continue
        if marker != 0x2C or offset + 9 > len(data):
            break
        frames += 1
        image_packed = data[offset + 8]
        offset += 9
        if image_packed & 0x80:
            offset += 3 * (2 ** ((image_packed & 0x07) + 1))
        if offset >= len(data):
            break
        offset += 1
        while offset < len(data):
            block_size = data[offset]
            offset += 1
            if block_size == 0:
                break
            offset += block_size
    return frames


PET_GIF_PATH = resource_path(os.path.join("assets", "phoebe_pet.gif"))
DEFAULT_PLATFORM = "学习通"
PLATFORM_DEFAULTS = {
    "学习通": {
        "api_method": "POST",
        "api_url": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
        "api_body": "courseType=1&courseFolderId=0&query=&pageHeader=-1&single=0&superstarClass=0&isFirefly=0",
    },
    "中国大学 MOOC": {
        "api_method": "POST",
        "api_url": "https://www.icourse163.org/web/j/learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc?csrfKey=",
        "api_body": "type=30&p=1&psize=8&courseType=2",
    },
    "智慧树": {
        "api_method": "POST",
        "api_url": "https://onlineservice-api.zhihuishu.com/gateway/t/v1/student/course/share/queryShareCourseInfo",
        "api_body": "secretStr=&date=",
    },
}
ASSIGNMENT_PLATFORM_DEFAULTS = {
    "学校作业平台": {
        "api_method": "GET",
        "api_url": "",
        "api_body": "",
    }
}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ASSIGNMENT_COMPLETED_STATUS = "已完成"

THEME = {
    "bg": "#edf6ff",
    "panel": "#f8fbff",
    "card": "#ffffff",
    "line": "#dce8fb",
    "text": "#1d2d4f",
    "muted": "#6e7f9f",
    "blue": "#4b85ff",
    "purple": "#8a6cff",
    "orange": "#f5ac38",
    "red": "#ee6b63",
    "green": "#35c68a",
}
CHECK_COLUMN = "checked"
CHECKED_TEXT = "☑"
UNCHECKED_TEXT = "☐"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect_db():
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, column_type):
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def init_db():
    with connect_db() as conn:
        conn.executescript(
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

            CREATE TABLE IF NOT EXISTS app_settings (
                user_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, setting_key),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS assignment_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                platform_name TEXT NOT NULL,
                task_type TEXT NOT NULL,
                course_name TEXT NOT NULL,
                task_title TEXT NOT NULL,
                task_url TEXT,
                publish_time TEXT,
                deadline_time TEXT,
                status TEXT NOT NULL DEFAULT '进行中',
                external_id TEXT NOT NULL,
                raw_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, platform_name, external_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        ensure_courses_schema(conn)
        for column in (
            "auth_cookie",
            "api_url",
            "referer",
            "user_agent",
            "api_method",
            "api_body",
            "last_sync_at",
        ):
            ensure_column(conn, "platform_accounts", column, "TEXT")
        conn.commit()


def get_desktop_user_id():
    with connect_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", ("desktop",)).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("desktop", "desktop-local-user", now_text()),
        )
        conn.commit()
        return cursor.lastrowid


parse_time = parse_datetime


def get_setting(user_id, key, default=""):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE user_id = ? AND setting_key = ?",
            (user_id, key),
        ).fetchone()
    return row["setting_value"] if row else default


def set_setting(user_id, key, value):
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (user_id, setting_key, setting_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (user_id, key, str(value), now_text()),
        )
        conn.commit()


def get_bool_setting(user_id, key, default=False):
    value = str(get_setting(user_id, key, "1" if default else "0")).strip().lower()
    return value in {"1", "true", "yes", "on", "是", "开启"}


def set_bool_setting(user_id, key, value):
    set_setting(user_id, key, "1" if value else "0")


def load_reminder_days(user_id):
    try:
        value = int(get_setting(user_id, "reminder_days", "7"))
    except ValueError:
        value = 7
    return min(30, max(1, value))


def nearest_time_sort_key(row, fields):
    now = datetime.now()
    future_times = []
    past_times = []
    for field in fields:
        try:
            value = row[field]
        except (KeyError, IndexError):
            value = None
        parsed = parse_time(value)
        if not parsed:
            continue
        if parsed >= now:
            future_times.append(parsed)
        else:
            past_times.append(parsed)
    if future_times:
        nearest = min(future_times)
        return (0, (nearest - now).total_seconds())
    if past_times:
        nearest = max(past_times)
        return (1, (now - nearest).total_seconds())
    return (2, float("inf"))


def reminder_state(target_time, reminder_days):
    if not target_time:
        return None
    now = datetime.now()
    seconds_left = (target_time - now).total_seconds()
    if seconds_left < 0:
        return {
            "state": "expired",
            "seconds_left": seconds_left,
            "days_left": 0,
        }
    remind_seconds = max(1, reminder_days) * 24 * 60 * 60
    if seconds_left <= remind_seconds:
        return {
            "state": "soon",
            "seconds_left": seconds_left,
            "days_left": int(seconds_left // (24 * 60 * 60)),
        }
    return None


def calculated_status(primary_time, reminder_days, soon_label):
    state = reminder_state(primary_time, reminder_days)
    if not primary_time:
        return "暂无时间"
    if not state:
        return "未到提醒"
    if state["state"] == "expired":
        return "已逾期"
    return soon_label


def calculated_course_status(row, reminder_days):
    deadline = parse_time(row["deadline_time"])
    if deadline:
        return calculated_status(deadline, reminder_days, "即将截止")
    exam_time = parse_time(row["exam_time"])
    if exam_time:
        return calculated_status(exam_time, reminder_days, "即将考试")
    return "暂无时间"


def calculated_assignment_status(row, reminder_days):
    deadline = parse_time(row["deadline_time"])
    return calculated_status(deadline, reminder_days, "即将截止")


def platform_default(platform_name, key):
    defaults = PLATFORM_DEFAULTS.get(platform_name, PLATFORM_DEFAULTS[DEFAULT_PLATFORM])
    if key == "user_agent":
        return DEFAULT_USER_AGENT
    return defaults.get(key, "")


def assignment_platform_default(platform_name, key):
    defaults = ASSIGNMENT_PLATFORM_DEFAULTS.get(platform_name, ASSIGNMENT_PLATFORM_DEFAULTS["学校作业平台"])
    if key == "user_agent":
        return DEFAULT_USER_AGENT
    return defaults.get(key, "")


AUTO_START_VALUE_NAME = "CourseReminder"
RUN_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    launcher = pythonw if os.path.exists(pythonw) else sys.executable
    script = os.path.join(BASE_DIR, "run_desktop.py")
    return f'"{launcher}" "{script}"'


def set_windows_auto_start(enabled):
    if os.name != "nt":
        raise OSError("当前系统不是 Windows，暂不支持开机自启动。")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTO_START_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, AUTO_START_VALUE_NAME)
            except FileNotFoundError:
                pass


def windows_auto_start_enabled():
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_READ) as key:
            value, _type = winreg.QueryValueEx(key, AUTO_START_VALUE_NAME)
        return bool(value)
    except FileNotFoundError:
        return False


class MiniReminderApp:
    def __init__(self, root):
        init_db()
        self.root = root
        self.user_id = get_desktop_user_id()
        self.reminder_days = tk.IntVar(value=load_reminder_days(self.user_id))
        self.auto_start_enabled = tk.BooleanVar(value=get_bool_setting(self.user_id, "auto_start_enabled", False))
        self.startup_auto_sync_enabled = tk.BooleanVar(
            value=get_bool_setting(self.user_id, "startup_auto_sync_enabled", True)
        )
        self.auto_start_button = None
        self._drag_start = None
        self.pet_image = None
        self.pet_images = []
        self.pet_animation_job = None
        self._configure_style()

        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry(self._bottom_right_geometry(500, 322))
        self.root.minsize(500, 322)
        self.root.configure(bg=THEME["bg"])
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        self._build_mini_window()
        self.root.after(0, self.position_main_window_bottom_right)
        self.root.after(1000, self.run_startup_sync_or_reminders)

    def position_main_window_bottom_right(self):
        self.root.geometry(self._bottom_right_geometry(500, 322))
        self.root.deiconify()
        self.root.lift()

    def _bottom_right_geometry(self, width, height):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(20, screen_width - width - 70)
        y = max(20, screen_height - height - 100)
        return f"{width}x{height}+{x}+{y}"

    def _center_geometry(self, width, height):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(20, (screen_width - width) // 2)
        y = max(20, (screen_height - height) // 2 - 20)
        return f"{width}x{height}+{x}+{y}"

    def _configure_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Wuwa.TFrame", background=THEME["panel"])
        style.configure("Wuwa.TLabel", background=THEME["panel"], foreground=THEME["text"], font=("Microsoft YaHei UI", 10))
        style.configure("WuwaMuted.TLabel", background=THEME["panel"], foreground=THEME["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Wuwa.TNotebook", background=THEME["panel"], borderwidth=0)
        style.configure("Wuwa.TNotebook.Tab", padding=(22, 10), font=("Microsoft YaHei UI", 12, "bold"))
        style.map("Wuwa.TNotebook.Tab", foreground=[("selected", THEME["blue"])], background=[("selected", "#ffffff")])
        style.configure(
            "Wuwa.Treeview",
            background="#fbfdff",
            fieldbackground="#fbfdff",
            foreground=THEME["text"],
            rowheight=30,
            borderwidth=0,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Wuwa.Treeview.Heading",
            background="#f2f7ff",
            foreground=THEME["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Wuwa.Treeview", background=[("selected", "#e8f1ff")], foreground=[("selected", THEME["text"])])

    def _build_mini_window(self):
        outer = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=10)
        outer.pack(fill="both", expand=True)
        outer.bind("<ButtonPress-1>", self._start_drag)
        outer.bind("<B1-Motion>", self._drag_window)

        title_bar = tk.Frame(outer, bg=THEME["bg"])
        title_bar.pack(fill="x")
        title_bar.bind("<ButtonPress-1>", self._start_drag)
        title_bar.bind("<B1-Motion>", self._drag_window)

        tk.Label(
            title_bar,
            text="🪶  网课任务弹窗提醒助手",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            title_bar,
            text="✦",
            bg=THEME["bg"],
            fg="#7ea8ff",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left", padx=(10, 0))

        close_btn = tk.Button(
            title_bar,
            text="×",
            command=self.root.destroy,
            bd=0,
            bg=THEME["bg"],
            fg="#7c8aa6",
            activebackground="#e4ecfb",
            font=("Microsoft YaHei UI", 14, "bold"),
            cursor="hand2",
        )
        close_btn.pack(side="right")

        body = tk.Frame(outer, bg=THEME["bg"])
        body.pack(fill="both", expand=True, pady=(8, 0))
        body.bind("<ButtonPress-1>", self._start_drag)
        body.bind("<B1-Motion>", self._drag_window)

        card = tk.Frame(body, bg=THEME["card"], highlightbackground=THEME["line"], highlightthickness=1)
        card.place(x=0, y=0, width=245, height=232)

        grid = tk.Frame(card, bg="white")
        grid.pack(fill="both", expand=True, padx=10, pady=10)

        self._tool_button(grid, "📋", "任务列表", self.open_task_list, 0, 0, THEME["blue"])
        self._tool_button(grid, "📘", "读取课程", self.open_sync_settings, 0, 1, THEME["blue"])
        self._tool_button(grid, "📄", "读取作业", self.open_assignment_sync_settings, 1, 0, THEME["purple"])
        self._tool_button(grid, "⏳", "即将截止", self.show_reminders, 1, 1, THEME["orange"])
        self._tool_button(grid, "🤖", "AI规划", self.open_ai_assistant, 2, 0, "#66a6ff")
        self.auto_start_button = self._tool_button(
            grid,
            "🚀",
            self._auto_start_button_text(),
            self.toggle_auto_start_from_mini,
            2,
            1,
            THEME["green"] if self.auto_start_enabled.get() else "#9eb2d6",
        )

        tip_count = len(self.build_reminders())
        tip_text = f"{tip_count} 个临期提醒" if tip_count else "暂无临期任务"
        self.tip_var = tk.StringVar(value=tip_text)
        tk.Label(
            body,
            textvariable=self.tip_var,
            bg="#eaf2ff",
            fg=THEME["blue"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).place(x=282, y=10, width=180, height=34)

        mascot = tk.Frame(body, bg=THEME["bg"])
        mascot.place(x=265, y=58, width=205, height=150)
        self._place_pet_image_or_draw(mascot, 205, 145)

        tk.Label(
            outer,
            text=f"v{APP_VERSION}     点击查看提醒 →",
            bg=THEME["bg"],
            fg="#6b91d9",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="bottom", pady=(0, 2))

    def _tool_button(self, parent, icon, text, command, row, column, color, columnspan=1):
        button = tk.Button(
            parent,
            text=f"{icon}\n{text}",
            command=command,
            bd=0,
            relief="flat",
            bg="#f7f9ff",
            activebackground="#edf2ff",
            fg="#1c2740",
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        button.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=5, pady=5)
        parent.grid_columnconfigure(column, weight=1)
        parent.grid_rowconfigure(row, weight=1)
        button.configure(highlightthickness=1, highlightbackground=color)
        return button

    def _auto_start_button_text(self):
        return "已自启" if self.auto_start_enabled.get() else "开机启动"

    def refresh_auto_start_button(self):
        if not self.auto_start_button:
            return
        self.auto_start_button.configure(
            text=f"🚀\n{self._auto_start_button_text()}",
            highlightbackground=THEME["green"] if self.auto_start_enabled.get() else "#9eb2d6",
        )

    def toggle_auto_start_from_mini(self):
        target = not self.auto_start_enabled.get()
        try:
            set_windows_auto_start(target)
        except Exception as exc:
            messagebox.showwarning("开机自启动设置失败", f"写入 Windows 启动项失败：{exc}", parent=self.root)
            return
        self.auto_start_enabled.set(target)
        set_bool_setting(self.user_id, "auto_start_enabled", target)
        self.refresh_auto_start_button()
        messagebox.showinfo(
            "开机自启动",
            f"已{'开启' if target else '关闭'}开机自启动。",
            parent=self.root,
        )

    def _place_pet_image_or_draw(self, parent, width, height):
        try:
            frames = []
            frame_count = gif_frame_count(PET_GIF_PATH)
            for frame_index in range(frame_count):
                frame = tk.PhotoImage(file=PET_GIF_PATH, format=f"gif -index {frame_index}")
                ratio = max(1, max(frame.width() // max(1, width), frame.height() // max(1, height)))
                frames.append(frame.subsample(ratio, ratio))
            if not frames:
                raise tk.TclError("GIF contains no readable frames")
            self.pet_images = frames
            self.pet_image = frames[0]
            label = tk.Label(parent, image=frames[0], bg=THEME["bg"], cursor="hand2")
            label.place(relx=0.5, rely=0.5, anchor="center")
            label.bind("<ButtonRelease-1>", lambda _event: self._play_pet_animation(label))
            label.bind("<ButtonPress-1>", self._start_drag)
            label.bind("<B1-Motion>", self._drag_window)
        except tk.TclError:
            mascot = tk.Canvas(parent, width=width, height=height, bg=THEME["bg"], highlightthickness=0, cursor="hand2")
            mascot.pack(fill="both", expand=True)
            self._draw_mascot(mascot)
            mascot.bind("<Button-1>", lambda _event: self.show_reminders())
            mascot.bind("<ButtonPress-1>", self._start_drag)
            mascot.bind("<B1-Motion>", self._drag_window)

    def _play_pet_animation(self, label):
        if self.pet_animation_job is not None:
            self.root.after_cancel(self.pet_animation_job)
        self._show_pet_frame(label, 0)
        self.show_reminders()

    def _show_pet_frame(self, label, frame_index):
        if not label.winfo_exists() or not self.pet_images:
            self.pet_animation_job = None
            return
        if frame_index >= len(self.pet_images):
            label.configure(image=self.pet_images[0])
            self.pet_image = self.pet_images[0]
            self.pet_animation_job = None
            return
        frame = self.pet_images[frame_index]
        label.configure(image=frame)
        self.pet_image = frame
        self.pet_animation_job = self.root.after(90, self._show_pet_frame, label, frame_index + 1)

    def _draw_mascot(self, canvas):
        canvas.create_oval(46, 108, 130, 132, fill="#d9e8ff", outline="")
        canvas.create_oval(32, 32, 118, 118, fill="#f7fbff", outline="#b8c9ea", width=2)
        canvas.create_oval(48, 58, 64, 74, fill="#2b364d", outline="")
        canvas.create_oval(88, 58, 104, 74, fill="#2b364d", outline="")
        canvas.create_arc(60, 72, 94, 96, start=205, extent=130, style="arc", width=2, outline="#f08ca0")
        canvas.create_polygon(36, 40, 14, 18, 50, 28, fill="#e9f2ff", outline="#b8c9ea")
        canvas.create_polygon(106, 28, 142, 18, 118, 40, fill="#e9f2ff", outline="#b8c9ea")
        canvas.create_text(75, 23, text="课", fill="#4f8cff", font=("Microsoft YaHei UI", 16, "bold"))
        canvas.create_text(75, 144, text="点我查看提醒", fill="#5b6882", font=("Microsoft YaHei UI", 9))

    def _start_drag(self, event):
        self._drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_window(self, event):
        if not self._drag_start:
            return
        start_x, start_y, win_x, win_y = self._drag_start
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        self.root.geometry(f"+{win_x + dx}+{win_y + dy}")

    def get_account_config(self, platform_name=DEFAULT_PLATFORM):
        with connect_db() as conn:
            account = conn.execute(
                "SELECT * FROM platform_accounts WHERE user_id = ? AND platform_name = ?",
                (self.user_id, platform_name),
            ).fetchone()
        if not account:
            return None
        stored_cookie = account["auth_cookie"] or ""
        return {
            "api_method": account["api_method"] or "POST",
            "api_url": account["api_url"] or platform_default(platform_name, "api_url"),
            "auth_cookie": get_secret(cookie_key("desktop", self.user_id, platform_name), stored_cookie),
            "api_body": account["api_body"] or platform_default(platform_name, "api_body"),
            "referer": account["referer"] or "",
            "user_agent": account["user_agent"] or DEFAULT_USER_AGENT,
        }

    def get_assignment_account_config(self, platform_name="学校作业平台"):
        with connect_db() as conn:
            account = conn.execute(
                "SELECT * FROM platform_accounts WHERE user_id = ? AND platform_name = ?",
                (self.user_id, platform_name),
            ).fetchone()
        if not account:
            return None
        stored_cookie = account["auth_cookie"] or ""
        return {
            "api_method": account["api_method"] or "GET",
            "api_url": account["api_url"] or assignment_platform_default(platform_name, "api_url"),
            "auth_cookie": get_secret(cookie_key("desktop", self.user_id, platform_name), stored_cookie),
            "api_body": account["api_body"] or assignment_platform_default(platform_name, "api_body"),
            "referer": account["referer"] or "",
            "user_agent": account["user_agent"] or DEFAULT_USER_AGENT,
        }

    def save_account(self, window, fields):
        platform_name = fields["platform"].get().strip() or DEFAULT_PLATFORM
        existing = self.get_account_config(platform_name) or {}
        config = {
            "api_method": fields["method"].get().strip() or "POST",
            "api_url": fields["api_url"].get().strip(),
            "auth_cookie": fields["cookie"].get("1.0", "end").strip() or existing.get("auth_cookie", ""),
            "api_body": fields["body"].get("1.0", "end").strip(),
            "referer": fields["referer"].get().strip(),
            "user_agent": fields["user_agent"].get().strip() or DEFAULT_USER_AGENT,
        }
        if not config["api_url"]:
            messagebox.showwarning("缺少接口 URL", f"请先填写{platform_name}课程接口 URL。", parent=window)
            return False
        if not config["auth_cookie"]:
            messagebox.showwarning("缺少 Cookie", f"请先填写从{platform_name}请求 Headers 里复制出来的 Cookie。", parent=window)
            return False
        try:
            validate_api_url(config["api_url"], platform_name)
        except ValueError as exc:
            messagebox.showwarning("接口地址不安全", str(exc), parent=window)
            return False
        if platform_name == "中国大学 MOOC" and "csrfKey=" not in config["api_url"] and "NTESSTUDYSI=" not in config["auth_cookie"]:
            messagebox.showwarning(
                "缺少 csrfKey",
                "中国大学 MOOC 需要接口 URL 包含 csrfKey，或 Cookie 中包含 NTESSTUDYSI。",
                parent=window,
            )
            return False

        timestamp = now_text()
        stored_in_keyring = set_secret(cookie_key("desktop", self.user_id, platform_name), config["auth_cookie"])
        database_cookie = "" if stored_in_keyring else config["auth_cookie"]
        with connect_db() as conn:
            conn.execute(
                """
                INSERT INTO platform_accounts
                    (user_id, platform_name, status, auth_cookie, api_url, referer,
                     user_agent, api_method, api_body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, platform_name) DO UPDATE SET
                    status = excluded.status,
                    auth_cookie = excluded.auth_cookie,
                    api_url = excluded.api_url,
                    referer = excluded.referer,
                    user_agent = excluded.user_agent,
                    api_method = excluded.api_method,
                    api_body = excluded.api_body,
                    updated_at = excluded.updated_at
                """,
                (
                    self.user_id,
                    platform_name,
                    "已绑定",
                    database_cookie,
                    config["api_url"],
                    config["referer"],
                    config["user_agent"],
                    config["api_method"],
                    config["api_body"],
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        messagebox.showinfo("保存成功", f"{platform_name}接口信息已保存。", parent=window)
        return True

    def clear_platform_cookie(self, platform_name, window, fields=None):
        delete_secret(cookie_key("desktop", self.user_id, platform_name))
        with connect_db() as conn:
            conn.execute(
                """
                UPDATE platform_accounts
                SET auth_cookie = '', status = 'Cookie 已清除', updated_at = ?
                WHERE user_id = ? AND platform_name = ?
                """,
                (now_text(), self.user_id, platform_name),
            )
            conn.commit()
        if fields and "cookie" in fields:
            fields["cookie"].delete("1.0", "end")
        if fields and "status_label" in fields:
            self.refresh_course_config_status(fields)
        messagebox.showinfo("Cookie 已清除", f"已清除 {platform_name} 的 Cookie。", parent=window)

    def save_assignment_account(self, window, fields):
        platform_name = fields["platform"].get().strip() or "学校作业平台"
        existing = self.get_assignment_account_config(platform_name) or {}
        config = {
            "api_method": fields["method"].get().strip() or "GET",
            "api_url": fields["api_url"].get().strip(),
            "auth_cookie": fields["cookie"].get("1.0", "end").strip() or existing.get("auth_cookie", ""),
            "api_body": fields["body"].get("1.0", "end").strip(),
            "referer": fields["referer"].get().strip(),
            "user_agent": fields["user_agent"].get().strip() or DEFAULT_USER_AGENT,
        }
        if not config["api_url"]:
            messagebox.showwarning("缺少接口 URL", "请先填写学校作业通知接口 URL。", parent=window)
            return False
        if not config["auth_cookie"]:
            messagebox.showwarning("缺少 Cookie", "请先填写从学校作业平台请求 Headers 里复制出来的 Cookie。", parent=window)
            return False
        try:
            validate_api_url(config["api_url"], platform_name)
        except ValueError as exc:
            messagebox.showwarning("接口地址不安全", str(exc), parent=window)
            return False

        timestamp = now_text()
        stored_in_keyring = set_secret(cookie_key("desktop", self.user_id, platform_name), config["auth_cookie"])
        database_cookie = "" if stored_in_keyring else config["auth_cookie"]
        with connect_db() as conn:
            conn.execute(
                """
                INSERT INTO platform_accounts
                    (user_id, platform_name, status, auth_cookie, api_url, referer,
                     user_agent, api_method, api_body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, platform_name) DO UPDATE SET
                    status = excluded.status,
                    auth_cookie = excluded.auth_cookie,
                    api_url = excluded.api_url,
                    referer = excluded.referer,
                    user_agent = excluded.user_agent,
                    api_method = excluded.api_method,
                    api_body = excluded.api_body,
                    updated_at = excluded.updated_at
                """,
                (
                    self.user_id,
                    platform_name,
                    "已绑定",
                    database_cookie,
                    config["api_url"],
                    config["referer"],
                    config["user_agent"],
                    config["api_method"],
                    config["api_body"],
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        messagebox.showinfo("保存成功", f"{platform_name}接口信息已保存。", parent=window)
        return True

    def open_sync_settings(self):
        window = tk.Toplevel(self.root)
        window.title("读取课程设置")
        window.geometry(self._center_geometry(650, 600))
        window.configure(bg=THEME["bg"])
        window.transient(self.root)

        platform_var = tk.StringVar(value=DEFAULT_PLATFORM)
        account = self.get_account_config(platform_var.get()) or {}
        fields = {
            "platform": platform_var,
            "method": tk.StringVar(value=account.get("api_method", platform_default(platform_var.get(), "api_method"))),
            "api_url": tk.StringVar(value=account.get("api_url", platform_default(platform_var.get(), "api_url"))),
            "referer": tk.StringVar(value=account.get("referer", "")),
            "user_agent": tk.StringVar(value=account.get("user_agent", DEFAULT_USER_AGENT)),
        }

        frame, actions = self._build_scrollable_form_shell(
            window,
            "读取课程",
            "填写平台接口信息后，点击保存并读取课程即可同步列表",
            "📖",
        )

        def load_selected_platform(*_args):
            platform_name = fields["platform"].get()
            selected_account = self.get_account_config(platform_name) or {}
            fields["method"].set(selected_account.get("api_method", platform_default(platform_name, "api_method")))
            fields["api_url"].set(selected_account.get("api_url", platform_default(platform_name, "api_url")))
            fields["referer"].set(selected_account.get("referer", ""))
            fields["user_agent"].set(selected_account.get("user_agent", DEFAULT_USER_AGENT))
            if "cookie" in fields:
                fields["cookie"].delete("1.0", "end")
            if "body" in fields:
                fields["body"].delete("1.0", "end")
                fields["body"].insert("1.0", selected_account.get("api_body", platform_default(platform_name, "api_body")))
            if "status_label" in fields:
                self.refresh_course_config_status(fields)

        def open_selected_platform():
            platform_name = fields["platform"].get()
            url = platform_login_url(platform_name)
            if not url:
                messagebox.showwarning("没有平台链接", f"暂未配置 {platform_name} 的打开链接。", parent=window)
                return
            webbrowser.open(url)

        self._form_label(frame, "平台")
        platform_row = tk.Frame(frame, bg="#ffffff")
        platform_row.pack(fill="x", pady=(4, 10))
        platform_box = ttk.Combobox(
            platform_row,
            textvariable=fields["platform"],
            values=supported_platforms(),
            state="readonly",
        )
        platform_box.pack(side="left", fill="x", expand=True, ipady=4)
        self._pill_button(platform_row, "打开平台", open_selected_platform, THEME["blue"]).pack(side="left", padx=(8, 0))
        platform_box.bind("<<ComboboxSelected>>", load_selected_platform)

        status_label = tk.Label(
            frame,
            text="",
            bg="#f8fbff",
            fg=THEME["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=6,
            anchor="w",
        )
        status_label.pack(fill="x", pady=(0, 10))
        fields["status_label"] = status_label
        self.refresh_course_config_status(fields)

        self._form_label(frame, "请求方式")
        ttk.Combobox(frame, textvariable=fields["method"], values=("POST", "GET"), state="readonly").pack(
            fill="x", pady=(4, 10), ipady=4
        )

        self._form_label(frame, "课程接口 URL")
        self._form_entry(frame, fields["api_url"]).pack(fill="x", pady=(4, 10))

        self._form_label(frame, "Cookie")
        cookie_text = self._form_text(frame, height=5)
        cookie_text.pack(fill="both", expand=True, pady=(4, 10))
        fields["cookie"] = cookie_text
        self._form_label(frame, "已保存的 Cookie 不会回显；留空表示保持原值")

        self._form_label(frame, "请求体参数")
        body_text = self._form_text(frame, height=3)
        body_text.pack(fill="x", pady=(4, 10))
        body_text.insert("1.0", account.get("api_body", platform_default(platform_var.get(), "api_body")))
        fields["body"] = body_text

        self._form_label(frame, "Referer")
        self._form_entry(frame, fields["referer"]).pack(fill="x", pady=(4, 10))

        self._form_label(frame, "User-Agent")
        self._form_entry(frame, fields["user_agent"]).pack(fill="x", pady=(4, 12))

        def save_and_refresh_status():
            if self.save_account(window, fields):
                self.refresh_course_config_status(fields)

        def sync_and_refresh_status():
            self.sync_courses(window, fields)
            self.refresh_course_config_status(fields)

        self._pill_button(actions, "保存配置", save_and_refresh_status, THEME["purple"]).pack(side="left", padx=(0, 8))
        self._pill_button(actions, "保存并读取当前平台课程", sync_and_refresh_status, THEME["blue"]).pack(side="left")
        self._pill_button(
            actions,
            "清除 Cookie",
            lambda: self.clear_platform_cookie(fields["platform"].get(), window, fields),
            THEME["red"],
        ).pack(side="left", padx=(8, 0))

    def open_assignment_sync_settings(self):
        window = tk.Toplevel(self.root)
        window.title("读取作业设置")
        window.geometry(self._center_geometry(650, 600))
        window.configure(bg=THEME["bg"])
        window.transient(self.root)

        platform_var = tk.StringVar(value="学校作业平台")
        account = self.get_assignment_account_config(platform_var.get()) or {}
        fields = {
            "platform": platform_var,
            "method": tk.StringVar(value=account.get("api_method", assignment_platform_default(platform_var.get(), "api_method"))),
            "api_url": tk.StringVar(value=account.get("api_url", assignment_platform_default(platform_var.get(), "api_url"))),
            "referer": tk.StringVar(value=account.get("referer", "")),
            "user_agent": tk.StringVar(value=account.get("user_agent", DEFAULT_USER_AGENT)),
        }

        frame, actions = self._build_scrollable_form_shell(
            window,
            "读取作业",
            "读取学校作业平台通知，自动同步未逾期的作业和测试",
            "📄",
        )

        self._form_label(frame, "作业平台")
        ttk.Combobox(
            frame,
            textvariable=fields["platform"],
            values=supported_assignment_platforms(),
            state="readonly",
        ).pack(fill="x", pady=(4, 10), ipady=4)

        self._form_label(frame, "请求方式")
        ttk.Combobox(frame, textvariable=fields["method"], values=("GET", "POST"), state="readonly").pack(
            fill="x", pady=(4, 10), ipady=4
        )

        self._form_label(frame, "作业通知接口 URL")
        self._form_entry(frame, fields["api_url"]).pack(fill="x", pady=(4, 10))

        self._form_label(frame, "Cookie")
        cookie_text = self._form_text(frame, height=5)
        cookie_text.pack(fill="both", expand=True, pady=(4, 10))
        fields["cookie"] = cookie_text
        self._form_label(frame, "已保存的 Cookie 不会回显；留空表示保持原值")

        self._form_label(frame, "请求体参数（GET 可留空）")
        body_text = self._form_text(frame, height=3)
        body_text.pack(fill="x", pady=(4, 10))
        body_text.insert("1.0", account.get("api_body", assignment_platform_default(platform_var.get(), "api_body")))
        fields["body"] = body_text

        self._form_label(frame, "Referer")
        self._form_entry(frame, fields["referer"]).pack(fill="x", pady=(4, 10))

        self._form_label(frame, "User-Agent")
        self._form_entry(frame, fields["user_agent"]).pack(fill="x", pady=(4, 12))

        self._pill_button(actions, "保存", lambda: self.save_assignment_account(window, fields), THEME["purple"]).pack(side="left", padx=(0, 8))
        self._pill_button(actions, "保存并读取作业", lambda: self.sync_assignments(window, fields), THEME["orange"]).pack(side="left")
        self._pill_button(
            actions,
            "清除 Cookie",
            lambda: self.clear_platform_cookie(fields["platform"].get(), window, fields),
            THEME["red"],
        ).pack(side="left", padx=(8, 0))

    def _build_form_shell(self, window, title, subtitle, icon):
        shell = tk.Frame(window, bg=THEME["bg"], padx=18, pady=16)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=THEME["bg"])
        header.pack(fill="x", pady=(0, 12))
        tk.Label(
            header,
            text=f"{icon}  {title}",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="✦",
            bg=THEME["bg"],
            fg="#8fb2ff",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="right")

        tk.Label(
            shell,
            text=subtitle,
            bg=THEME["bg"],
            fg=THEME["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(0, 10))

        card = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1, padx=16, pady=14)
        card.pack(fill="both", expand=True)
        return card

    def _build_scrollable_form_shell(self, window, title, subtitle, icon):
        shell = tk.Frame(window, bg=THEME["bg"], padx=18, pady=16)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=THEME["bg"])
        header.pack(fill="x", pady=(0, 12))
        tk.Label(
            header,
            text=f"{icon}  {title}",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="✦",
            bg=THEME["bg"],
            fg="#8fb2ff",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="right")

        tk.Label(
            shell,
            text=subtitle,
            bg=THEME["bg"],
            fg=THEME["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(0, 10))

        card = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1)
        card.pack(fill="both", expand=True)

        canvas = tk.Canvas(card, bg="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg="#ffffff", padx=16, pady=14)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def resize_body(event):
            canvas.itemconfigure(body_id, width=event.width)

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", resize_body)
        body.bind("<Configure>", update_scroll_region)

        actions = tk.Frame(shell, bg=THEME["bg"], pady=10)
        actions.pack(fill="x")
        return body, actions

    def refresh_course_config_status(self, fields):
        platform_name = fields["platform"].get()
        account = self.get_account_config(platform_name)
        label = fields.get("status_label")
        if not label:
            return
        if not account:
            text = f"当前平台：{platform_name} 未保存配置"
            color = THEME["orange"]
        elif not (account.get("auth_cookie") or "").strip():
            text = f"当前平台：{platform_name} 未保存 Cookie"
            color = THEME["orange"]
        elif not (account.get("api_url") or "").strip():
            text = f"当前平台：{platform_name} 未保存接口 URL"
            color = THEME["orange"]
        else:
            text = f"当前平台：{platform_name} 已保存，可自动同步"
            color = THEME["green"]
        label.configure(text=text, fg=color)

    def _form_label(self, parent, text):
        tk.Label(
            parent,
            text=text,
            bg="#ffffff",
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")

    def _form_entry(self, parent, variable):
        return tk.Entry(
            parent,
            textvariable=variable,
            bg="#f8fbff",
            fg=THEME["text"],
            insertbackground=THEME["blue"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["line"],
            highlightcolor="#9ebcff",
            font=("Microsoft YaHei UI", 10),
        )

    def _form_text(self, parent, height):
        return tk.Text(
            parent,
            height=height,
            wrap="word",
            bg="#f8fbff",
            fg=THEME["text"],
            insertbackground=THEME["blue"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["line"],
            highlightcolor="#9ebcff",
            font=("Microsoft YaHei UI", 9),
        )

    def _pill_button(self, parent, text, command, color):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=18,
            pady=8,
            cursor="hand2",
        )

    def sync_courses(self, window=None, fields=None):
        if fields and not self.save_account(window, fields):
            return
        platform_name = fields["platform"].get().strip() if fields else DEFAULT_PLATFORM
        config = self.get_account_config(platform_name)
        if not config or not config["auth_cookie"]:
            self.open_sync_settings()
            return

        try:
            courses = crawl_platform_courses(platform_name, config)
        except ScraperError as exc:
            messagebox.showerror("同步失败", str(exc), parent=window or self.root)
            if messagebox.askyesno("重新填写", "是否打开课程同步设置，重新填写 Cookie 或接口信息？", parent=window or self.root):
                self.open_sync_settings()
            return
        except Exception as exc:
            messagebox.showerror("同步失败", f"发生未知错误：{exc}", parent=window or self.root)
            return

        courses = self.save_courses_for_platform(platform_name, courses)
        self.refresh_tip()
        messagebox.showinfo("同步完成", f"已读取并保存 {len(courses)} 门{platform_name}课程。", parent=window or self.root)

    def sync_assignments(self, window=None, fields=None):
        if fields and not self.save_assignment_account(window, fields):
            return
        platform_name = fields["platform"].get().strip() if fields else "学校作业平台"
        config = self.get_assignment_account_config(platform_name)
        if not config or not config["auth_cookie"]:
            self.open_assignment_sync_settings()
            return

        try:
            tasks = crawl_assignment_tasks(platform_name, config)
        except ScraperError as exc:
            messagebox.showerror("读取作业失败", str(exc), parent=window or self.root)
            if messagebox.askyesno("重新填写", "是否打开读取作业设置，重新填写 Cookie 或接口信息？", parent=window or self.root):
                self.open_assignment_sync_settings()
            return
        except Exception as exc:
            messagebox.showerror("读取作业失败", f"发生未知错误：{exc}", parent=window or self.root)
            return

        tasks = self.save_assignments_for_platform(platform_name, tasks)
        self.refresh_tip()
        messagebox.showinfo("读取完成", f"已读取并保存 {len(tasks)} 条{platform_name}作业/测试任务。", parent=window or self.root)

    def save_courses_for_platform(self, platform_name, courses):
        courses = self.dedupe_fetched_courses(courses)
        timestamp = now_text()
        with connect_db() as conn:
            for item in courses:
                conn.execute(
                    """
                    INSERT INTO courses
                        (user_id, platform_name, external_id, course_name, course_url, teacher, progress,
                         deadline_time, exam_time, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform_name, external_id) DO UPDATE SET
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
                        self.user_id,
                        platform_name,
                        item.get("external_id") or fallback_course_external_id(
                            platform_name, item.get("course_name"), item.get("course_url")
                        ),
                        item.get("course_name") or "未命名课程",
                        item.get("course_url") or "",
                        item.get("teacher") or "",
                        int(item.get("progress") or 0),
                        item.get("deadline_time"),
                        item.get("exam_time"),
                        item.get("status") or "进行中",
                        timestamp,
                        timestamp,
                    ),
                )
            conn.execute(
                """
                UPDATE platform_accounts
                SET status = ?, last_sync_at = ?, updated_at = ?
                WHERE user_id = ? AND platform_name = ?
                """,
                ("已获取课程", timestamp, timestamp, self.user_id, platform_name),
            )
            conn.commit()
        return courses

    def save_assignments_for_platform(self, platform_name, tasks):
        timestamp = now_text()
        with connect_db() as conn:
            self.mark_expired_assignments(conn, platform_name)
            for item in tasks:
                conn.execute(
                    """
                    INSERT INTO assignment_tasks
                        (user_id, platform_name, task_type, course_name, task_title, task_url,
                         publish_time, deadline_time, status, external_id, raw_text, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform_name, external_id) DO UPDATE SET
                        task_type = excluded.task_type,
                        course_name = excluded.course_name,
                        task_title = excluded.task_title,
                        task_url = excluded.task_url,
                        publish_time = excluded.publish_time,
                        deadline_time = excluded.deadline_time,
                        status = CASE
                            WHEN assignment_tasks.status = ? THEN assignment_tasks.status
                            ELSE excluded.status
                        END,
                        raw_text = excluded.raw_text,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.user_id,
                        platform_name,
                        item.get("task_type") or "作业",
                        item.get("course_name") or "未知课程",
                        item.get("task_title") or "未命名任务",
                        item.get("task_url") or "",
                        item.get("publish_time"),
                        item.get("deadline_time"),
                        item.get("status") or "进行中",
                        item.get("external_id"),
                        item.get("raw_text") or "",
                        timestamp,
                        timestamp,
                        ASSIGNMENT_COMPLETED_STATUS,
                    ),
                )
            conn.execute(
                """
                UPDATE platform_accounts
                SET status = ?, last_sync_at = ?, updated_at = ?
                WHERE user_id = ? AND platform_name = ?
                """,
                ("已获取作业", timestamp, timestamp, self.user_id, platform_name),
            )
            conn.commit()
        return tasks

    def update_platform_sync_status(self, platform_name, status):
        timestamp = now_text()
        with connect_db() as conn:
            conn.execute(
                """
                UPDATE platform_accounts
                SET status = ?, updated_at = ?
                WHERE user_id = ? AND platform_name = ?
                """,
                (status[:80], timestamp, self.user_id, platform_name),
            )
            conn.commit()

    def mark_expired_assignments(self, conn, platform_name=None):
        now = now_text()
        if platform_name:
            conn.execute(
                """
                UPDATE assignment_tasks
                SET status = ?, updated_at = ?
                WHERE user_id = ?
                  AND platform_name = ?
                  AND deadline_time IS NOT NULL
                  AND deadline_time < ?
                  AND status != ?
                  AND status != ?
                """,
                ("已截止", now, self.user_id, platform_name, now, "已截止", ASSIGNMENT_COMPLETED_STATUS),
            )
        else:
            conn.execute(
                """
                UPDATE assignment_tasks
                SET status = ?, updated_at = ?
                WHERE user_id = ?
                  AND deadline_time IS NOT NULL
                  AND deadline_time < ?
                  AND status != ?
                  AND status != ?
                """,
                ("已截止", now, self.user_id, now, "已截止", ASSIGNMENT_COMPLETED_STATUS),
            )

    def run_startup_sync_or_reminders(self):
        if self.startup_auto_sync_enabled.get():
            self.start_startup_auto_sync()
        else:
            self.show_startup_reminders()

    def start_startup_auto_sync(self):
        if hasattr(self, "tip_var"):
            self.tip_var.set("正在同步任务...")

        def worker():
            course_count = 0
            assignment_count = 0
            failures = []

            for platform_name in supported_platforms():
                config = self.get_account_config(platform_name)
                if not self._syncable_config(config):
                    continue
                try:
                    courses = crawl_platform_courses(platform_name, config)
                    saved = self.save_courses_for_platform(platform_name, courses)
                    course_count += len(saved)
                except Exception as exc:
                    failures.append(f"{platform_name}: {exc}")
                    self.update_platform_sync_status(platform_name, f"自动同步失败：{exc}")

            for platform_name in supported_assignment_platforms():
                config = self.get_assignment_account_config(platform_name)
                if not self._syncable_config(config):
                    continue
                try:
                    tasks = crawl_assignment_tasks(platform_name, config)
                    saved = self.save_assignments_for_platform(platform_name, tasks)
                    assignment_count += len(saved)
                except Exception as exc:
                    failures.append(f"{platform_name}: {exc}")
                    self.update_platform_sync_status(platform_name, f"自动同步失败：{exc}")

            def finish():
                reminders = self.build_reminders()
                reminder_text = f"{len(reminders)} 个临期提醒" if reminders else "暂无临期任务"
                if hasattr(self, "tip_var"):
                    self.tip_var.set(f"已同步，{reminder_text}")
                set_setting(
                    self.user_id,
                    "last_startup_sync_summary",
                    f"课程 {course_count} 门，作业 {assignment_count} 条，失败 {len(failures)} 个；{now_text()}",
                )
                if reminders:
                    self._show_reminder_window(reminders[:8])

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _syncable_config(self, config):
        return bool(config and (config.get("auth_cookie") or "").strip() and (config.get("api_url") or "").strip())

    def dedupe_fetched_courses(self, courses):
        best = {}
        for item in courses:
            key = item.get("external_id") or fallback_course_external_id(
                "", item.get("course_name") or "未命名课程", item.get("course_url")
            )
            old = best.get(key)
            if old is None or self.course_quality_score(item) > self.course_quality_score(old):
                best[key] = item
        return list(best.values())

    def cleanup_duplicate_courses(self, conn, platform_name):
        rows = conn.execute(
            """
            SELECT * FROM courses
            WHERE user_id = ? AND platform_name = ?
            ORDER BY id ASC
            """,
            (self.user_id, platform_name),
        ).fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(self.course_name_key(row["course_name"]), []).append(row)

        for group in groups.values():
            if len(group) <= 1:
                continue
            keep = max(group, key=self.stored_course_quality_score)
            delete_ids = [row["id"] for row in group if row["id"] != keep["id"]]
            placeholders = ",".join("?" for _ in delete_ids)
            conn.execute(
                f"DELETE FROM courses WHERE user_id = ? AND platform_name = ? AND id IN ({placeholders})",
                [self.user_id, platform_name, *delete_ids],
            )

    def course_name_key(self, name):
        return "".join(str(name or "").split()).lower()

    def course_quality_score(self, item):
        return (
            (10 if item.get("deadline_time") else 0)
            + (5 if item.get("teacher") else 0)
            + (2 if item.get("course_url") else 0)
            + (1 if item.get("status") == "已结束" else 0)
        )

    def stored_course_quality_score(self, row):
        return (
            (10 if row["deadline_time"] else 0)
            + (5 if row["teacher"] else 0)
            + (2 if row["course_url"] else 0)
            + (1 if row["status"] == "已结束" else 0)
            + row["id"] * 0.000001
        )

    def open_task_list(self):
        with connect_db() as conn:
            self.mark_expired_assignments(conn)
            conn.commit()
            course_count = conn.execute("SELECT COUNT(*) AS total FROM courses WHERE user_id = ?", (self.user_id,)).fetchone()["total"]
            assignment_count = conn.execute(
                "SELECT COUNT(*) AS total FROM assignment_tasks WHERE user_id = ? AND status != ?",
                (self.user_id, ASSIGNMENT_COMPLETED_STATUS),
            ).fetchone()["total"]
            completed_assignment_count = conn.execute(
                "SELECT COUNT(*) AS total FROM assignment_tasks WHERE user_id = ? AND status = ?",
                (self.user_id, ASSIGNMENT_COMPLETED_STATUS),
            ).fetchone()["total"]

        window = tk.Toplevel(self.root)
        window.title(f"{APP_NAME} v{APP_VERSION}")
        window.geometry(self._center_geometry(760, 520))
        window.minsize(700, 470)
        window.configure(bg=THEME["bg"])
        window.transient(self.root)

        shell = tk.Frame(window, bg=THEME["bg"], padx=14, pady=12)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=THEME["bg"])
        header.pack(fill="x")
        tk.Label(
            header,
            text=f"🪶  {APP_NAME}  v{APP_VERSION}",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="为你的学习保驾护航 ✧",
            bg="#eef6ff",
            fg="#638de5",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=3,
        ).pack(side="left", padx=(18, 0))

        hero = tk.Frame(shell, bg=THEME["bg"], height=82)
        hero.pack(fill="x", pady=(10, 8))
        hero.pack_propagate(False)

        button_card = tk.Frame(hero, bg="#ffffff", highlightthickness=0)
        button_card.place(x=0, y=8, width=520, height=54)
        self._hero_button(button_card, "📖", "读取课程", self.open_sync_settings, 10, THEME["blue"])
        self._hero_button(button_card, "📄", "读取作业", self.open_assignment_sync_settings, 135, THEME["purple"])
        self._hero_button(button_card, "🔔", "提醒设置", self.open_reminder_settings, 260, THEME["orange"])
        self._hero_button(button_card, "🤖", "AI规划", self.open_ai_assistant, 385, "#66a6ff")

        tk.Label(
            hero,
            text="✧ 今天也要\n加油学习鸭",
            bg=THEME["bg"],
            fg="#6284d5",
            font=("Microsoft YaHei UI", 12, "bold"),
            justify="center",
        ).place(x=530, y=12, width=110, height=54)
        pet_holder = tk.Frame(hero, bg=THEME["bg"])
        pet_holder.place(x=645, y=0, width=90, height=78)
        self._place_pet_image_or_draw(pet_holder, 135, 76)

        content = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1)
        content.pack(fill="both", expand=True)

        notebook = ttk.Notebook(content, style="Wuwa.TNotebook")
        notebook.pack(fill="both", expand=True, padx=9, pady=9)

        course_frame = ttk.Frame(notebook, padding=8, style="Wuwa.TFrame")
        assignment_frame = ttk.Frame(notebook, padding=8, style="Wuwa.TFrame")
        completed_assignment_frame = ttk.Frame(notebook, padding=8, style="Wuwa.TFrame")
        notebook.add(course_frame, text="课程任务")
        notebook.add(assignment_frame, text="作业任务")
        notebook.add(completed_assignment_frame, text="已完成")

        course_actions = tk.Frame(course_frame, bg=THEME["panel"])
        course_actions.pack(fill="x", pady=(0, 8))
        tk.Label(
            course_actions,
            text=f"⟳  共 {course_count} 条任务",
            bg="#edf4ff",
            fg="#5c7fc6",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=5,
        ).pack(side="left")
        self._small_action_button(course_actions, "刷新", lambda: self.fill_course_tree(course_tree)).pack(side="right", padx=(8, 0))
        self._small_action_button(
            course_actions,
            "删除所选项目",
            lambda: self.delete_selected_courses(course_tree, window),
            danger=True,
        ).pack(side="right")

        course_table = tk.Frame(course_frame, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1)
        course_table.pack(fill="both", expand=True)
        course_columns = ("platform_name", "course_name", "teacher", "deadline_time", "exam_time", "status")
        course_tree = ttk.Treeview(
            course_table,
            columns=course_columns,
            show="headings",
            selectmode="browse",
            style="Wuwa.Treeview",
        )
        course_tree.pack(fill="both", expand=True)
        course_headings = {
            "course_name": "课程",
            "platform_name": "平台",
            "teacher": "教师/信息",
            "deadline_time": "截止",
            "exam_time": "考试",
            "status": "状态",
        }
        course_widths = {"platform_name": 105, "course_name": 175, "teacher": 135, "deadline_time": 135, "exam_time": 78, "status": 72}
        course_tree.task_columns = course_columns
        course_tree.task_headings = course_headings
        course_tree.task_widths = course_widths
        course_tree.multi_select_mode = False
        course_tree.checked_ids = set()
        course_multi_button = self._small_action_button(
            course_actions,
            "多选",
            lambda: self.toggle_tree_multi_select(course_tree, course_multi_button, self.fill_course_tree),
        )
        course_multi_button.pack(side="right", padx=(0, 8))
        self.configure_task_tree_columns(course_tree)
        self.fill_course_tree(course_tree)
        course_tree.bind("<Button-1>", lambda event: self.handle_tree_checkbox_click(course_tree, event))

        assignment_actions = tk.Frame(assignment_frame, bg=THEME["panel"])
        assignment_actions.pack(fill="x", pady=(0, 8))
        tk.Label(
            assignment_actions,
            text=f"⟳  共 {assignment_count} 条任务",
            bg="#edf4ff",
            fg="#5c7fc6",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=5,
        ).pack(side="left")
        self._small_action_button(assignment_actions, "刷新", lambda: self.fill_assignment_tree(assignment_tree)).pack(
            side="right", padx=(8, 0)
        )
        self._small_action_button(
            assignment_actions,
            "删除所选项目",
            lambda: self.delete_selected_assignments(assignment_tree, window),
            danger=True,
        ).pack(side="right")

        assignment_columns = ("platform_name", "course_name", "task_type", "task_title", "deadline_time", "status")
        assignment_table = tk.Frame(assignment_frame, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1)
        assignment_table.pack(fill="both", expand=True)
        assignment_tree = ttk.Treeview(
            assignment_table,
            columns=assignment_columns,
            show="headings",
            selectmode="browse",
            style="Wuwa.Treeview",
        )
        assignment_tree.pack(fill="both", expand=True)
        assignment_headings = {
            "platform_name": "平台",
            "course_name": "课程",
            "task_type": "类型",
            "task_title": "作业/测试",
            "deadline_time": "截止",
            "status": "状态",
        }
        assignment_widths = {
            "platform_name": 105,
            "course_name": 155,
            "task_type": 70,
            "task_title": 215,
            "deadline_time": 135,
            "status": 72,
        }
        assignment_tree.task_columns = assignment_columns
        assignment_tree.task_headings = assignment_headings
        assignment_tree.task_widths = assignment_widths
        assignment_tree.multi_select_mode = False
        assignment_tree.checked_ids = set()
        assignment_multi_button = self._small_action_button(
            assignment_actions,
            "多选",
            lambda: self.toggle_tree_multi_select(assignment_tree, assignment_multi_button, self.fill_assignment_tree),
        )
        assignment_multi_button.pack(side="right", padx=(0, 8))
        self.configure_task_tree_columns(assignment_tree)
        self.fill_assignment_tree(assignment_tree)
        assignment_tree.bind("<Button-1>", lambda event: self.handle_tree_checkbox_click(assignment_tree, event))

        completed_actions = tk.Frame(completed_assignment_frame, bg=THEME["panel"])
        completed_actions.pack(fill="x", pady=(0, 8))
        tk.Label(
            completed_actions,
            text=f"✓  共 {completed_assignment_count} 条已完成",
            bg="#edf4ff",
            fg="#5c7fc6",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=5,
        ).pack(side="left")

        completed_assignment_table = tk.Frame(
            completed_assignment_frame,
            bg="#ffffff",
            highlightbackground=THEME["line"],
            highlightthickness=1,
        )
        completed_assignment_table.pack(fill="both", expand=True)
        completed_assignment_tree = ttk.Treeview(
            completed_assignment_table,
            columns=assignment_columns,
            show="headings",
            selectmode="browse",
            style="Wuwa.Treeview",
        )
        completed_assignment_tree.pack(fill="both", expand=True)
        completed_assignment_tree.task_columns = assignment_columns
        completed_assignment_tree.task_headings = assignment_headings
        completed_assignment_tree.task_widths = assignment_widths
        completed_assignment_tree.multi_select_mode = False
        completed_assignment_tree.checked_ids = set()
        self.configure_task_tree_columns(completed_assignment_tree)
        self.fill_assignment_tree(completed_assignment_tree, completed=True)
        self._small_action_button(
            completed_actions,
            "刷新",
            lambda: self.fill_assignment_tree(completed_assignment_tree, completed=True),
        ).pack(side="right")
        assignment_tree.bind(
            "<Button-3>",
            lambda event: self.show_assignment_context_menu(event, assignment_tree, completed_assignment_tree),
        )

        footer = tk.Frame(shell, bg=THEME["bg"])
        footer.pack(fill="x", pady=(10, 0))
        tk.Label(
            footer,
            text="💡 点击「多选」后会显示勾选框，可一次勾选多个项目并删除",
            bg=THEME["bg"],
            fg="#6a87c5",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        tk.Label(
            footer,
            text=f"最后检查：{now_text()}  ⟳",
            bg=THEME["bg"],
            fg="#8a9abb",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right")

    def _hero_button(self, parent, icon, text, command, x, color):
        button = tk.Button(
            parent,
            text=f"{icon}  {text}",
            command=command,
            bd=0,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        button.place(x=x, y=9, width=118, height=36)
        return button

    def _small_action_button(self, parent, text, command, danger=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            bg="#fff1ef" if danger else "#f8fbff",
            fg=THEME["red"] if danger else THEME["text"],
            activebackground="#ffe8e5" if danger else "#edf4ff",
            font=("Microsoft YaHei UI", 10),
            padx=14,
            pady=6,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground="#ffd8d2" if danger else THEME["line"],
        )

    def configure_task_tree_columns(self, tree):
        columns = tuple(getattr(tree, "task_columns", ()))
        headings = getattr(tree, "task_headings", {})
        widths = getattr(tree, "task_widths", {})
        if getattr(tree, "multi_select_mode", False):
            display_columns = (CHECK_COLUMN, *columns)
            tree.configure(columns=display_columns, selectmode="none")
            tree.heading(CHECK_COLUMN, text="选择")
            tree.column(CHECK_COLUMN, width=48, minwidth=48, anchor="center", stretch=False)
        else:
            tree.configure(columns=columns, selectmode="browse")
        for column in columns:
            tree.heading(column, text=headings.get(column, column))
            tree.column(column, width=widths.get(column, 110), anchor="w")

    def toggle_tree_multi_select(self, tree, button, fill_func):
        tree.multi_select_mode = not getattr(tree, "multi_select_mode", False)
        tree.checked_ids = set()
        self.configure_task_tree_columns(tree)
        fill_func(tree)
        button.configure(text="退出多选" if tree.multi_select_mode else "多选")

    def handle_tree_checkbox_click(self, tree, event):
        if not getattr(tree, "multi_select_mode", False):
            return None
        if tree.identify_region(event.x, event.y) != "cell":
            return "break"
        if tree.identify_column(event.x) != "#1":
            return "break"
        row_id = tree.identify_row(event.y)
        if not row_id:
            return "break"
        checked_ids = getattr(tree, "checked_ids", set())
        if row_id in checked_ids:
            checked_ids.remove(row_id)
        else:
            checked_ids.add(row_id)
        tree.checked_ids = checked_ids
        values = list(tree.item(row_id, "values"))
        if values:
            values[0] = CHECKED_TEXT if row_id in checked_ids else UNCHECKED_TEXT
            tree.item(row_id, values=values)
        return "break"

    def selected_task_ids(self, tree):
        if getattr(tree, "multi_select_mode", False):
            return list(getattr(tree, "checked_ids", set()))
        return list(tree.selection())

    def fill_course_tree(self, tree):
        for item_id in tree.get_children():
            tree.delete(item_id)
        with connect_db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM courses
                WHERE user_id = ? AND status NOT IN ('已完成', '已结束')
                ORDER BY updated_at DESC
                """,
                (self.user_id,),
            ).fetchall()
        rows = sorted(rows, key=lambda row: nearest_time_sort_key(row, ("deadline_time", "exam_time")))
        reminder_days = max(1, self.reminder_days.get())
        multi_mode = getattr(tree, "multi_select_mode", False)
        checked_ids = getattr(tree, "checked_ids", set())
        for row in rows:
            item_id = str(row["id"])
            values = (
                row["platform_name"],
                row["course_name"],
                row["teacher"] or "暂无",
                row["deadline_time"] or "暂无",
                row["exam_time"] or "暂无",
                calculated_course_status(row, reminder_days),
            )
            if multi_mode:
                values = (CHECKED_TEXT if item_id in checked_ids else UNCHECKED_TEXT, *values)
            tree.insert(
                "",
                "end",
                iid=item_id,
                values=values,
            )

    def delete_selected_courses(self, tree, window):
        selected = self.selected_task_ids(tree)
        if not selected:
            messagebox.showwarning("未选择项目", "请先选择要删除的课程项目。", parent=window)
            return
        if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(selected)} 个课程项目吗？", parent=window):
            return
        ids = [int(item_id) for item_id in selected]
        placeholders = ",".join("?" for _ in ids)
        with connect_db() as conn:
            conn.execute(
                f"DELETE FROM courses WHERE user_id = ? AND id IN ({placeholders})",
                [self.user_id, *ids],
            )
            conn.commit()
        tree.checked_ids = set()
        self.fill_course_tree(tree)
        self.refresh_tip()

    def fill_assignment_tree(self, tree, completed=False):
        for item_id in tree.get_children():
            tree.delete(item_id)
        with connect_db() as conn:
            status_operator = "=" if completed else "!="
            rows = conn.execute(
                f"""
                SELECT * FROM assignment_tasks
                WHERE user_id = ?
                  AND status {status_operator} ?
                ORDER BY updated_at DESC
                """,
                (self.user_id, ASSIGNMENT_COMPLETED_STATUS),
            ).fetchall()
        rows = sorted(rows, key=lambda row: nearest_time_sort_key(row, ("deadline_time",)))
        reminder_days = max(1, self.reminder_days.get())
        multi_mode = getattr(tree, "multi_select_mode", False)
        checked_ids = getattr(tree, "checked_ids", set())
        for row in rows:
            item_id = str(row["id"])
            values = (
                row["platform_name"],
                row["course_name"],
                row["task_type"],
                row["task_title"],
                row["deadline_time"] or "暂无",
                ASSIGNMENT_COMPLETED_STATUS if completed else calculated_assignment_status(row, reminder_days),
            )
            if multi_mode:
                values = (CHECKED_TEXT if item_id in checked_ids else UNCHECKED_TEXT, *values)
            tree.insert(
                "",
                "end",
                iid=item_id,
                values=values,
            )

    def show_assignment_context_menu(self, event, assignment_tree, completed_assignment_tree):
        row_id = assignment_tree.identify_row(event.y)
        if not row_id:
            return
        assignment_tree.selection_set(row_id)
        menu = tk.Menu(assignment_tree, tearoff=False)
        menu.add_command(
            label="已完成",
            command=lambda: self.mark_assignment_completed(row_id, assignment_tree, completed_assignment_tree),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def mark_assignment_completed(self, item_id, assignment_tree, completed_assignment_tree):
        timestamp = now_text()
        with connect_db() as conn:
            conn.execute(
                """
                UPDATE assignment_tasks
                SET status = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (ASSIGNMENT_COMPLETED_STATUS, timestamp, self.user_id, int(item_id)),
            )
            conn.commit()
        assignment_tree.checked_ids = set()
        completed_assignment_tree.checked_ids = set()
        self.fill_assignment_tree(assignment_tree)
        self.fill_assignment_tree(completed_assignment_tree, completed=True)
        self.refresh_tip()

    def delete_selected_assignments(self, tree, window):
        selected = self.selected_task_ids(tree)
        if not selected:
            messagebox.showwarning("未选择项目", "请先选择要删除的作业/测试项目。", parent=window)
            return
        if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(selected)} 个作业/测试项目吗？", parent=window):
            return
        ids = [int(item_id) for item_id in selected]
        placeholders = ",".join("?" for _ in ids)
        with connect_db() as conn:
            conn.execute(
                f"DELETE FROM assignment_tasks WHERE user_id = ? AND id IN ({placeholders})",
                [self.user_id, *ids],
            )
            conn.commit()
        tree.checked_ids = set()
        self.fill_assignment_tree(tree)
        self.refresh_tip()

    def open_reminder_settings(self):
        window = tk.Toplevel(self.root)
        window.title("提醒设置")
        window.geometry(self._center_geometry(460, 390))
        window.configure(bg=THEME["bg"])
        window.transient(self.root)

        frame = self._build_form_shell(
            window,
            "提醒设置",
            "自定义任务截止前多少天开始提醒",
            "🔔",
        )
        tk.Label(
            frame,
            text="提前提醒天数",
            bg="#ffffff",
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        row = tk.Frame(frame, bg="#ffffff")
        row.pack(fill="x", pady=(0, 14))
        spin = ttk.Spinbox(row, from_=1, to=30, textvariable=self.reminder_days, width=8)
        spin.pack(side="left", ipady=5)
        tk.Label(
            row,
            text="天内显示为即将截止，已过时间会标记为已逾期",
            bg="#ffffff",
            fg=THEME["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(12, 0))

        tk.Label(
            frame,
            text="打开程序会自动检查，你也可以在小浮窗点击「即将截止」手动查看。",
            bg="#ffffff",
            fg="#6a87c5",
            font=("Microsoft YaHei UI", 9),
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        tk.Checkbutton(
            frame,
            text="开机自动启动网课提醒助手",
            variable=self.auto_start_enabled,
            bg="#ffffff",
            fg=THEME["text"],
            activebackground="#ffffff",
            selectcolor="#eef6ff",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(0, 8))

        tk.Checkbutton(
            frame,
            text="打开软件时自动同步课程和作业",
            variable=self.startup_auto_sync_enabled,
            bg="#ffffff",
            fg=THEME["text"],
            activebackground="#ffffff",
            selectcolor="#eef6ff",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(0, 16))

        actions = tk.Frame(frame, bg="#ffffff")
        actions.pack(fill="x")
        self._pill_button(actions, "保存提醒设置", lambda: self.save_reminder_settings(window), THEME["blue"]).pack(
            side="left", padx=(0, 8)
        )
        self._pill_button(actions, "立即检查", self.show_reminders, THEME["orange"]).pack(side="left")

    def open_ai_assistant(self):
        window = tk.Toplevel(self.root)
        window.title("AI学习规划助手")
        window.geometry(self._center_geometry(650, 680))
        window.minsize(560, 620)
        window.configure(bg=THEME["bg"])
        window.transient(self.root)

        shell = tk.Frame(window, bg=THEME["bg"], padx=16, pady=14)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=THEME["bg"])
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header,
            text="🤖  AI学习规划助手",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="温柔整理任务，陪你一步一步完成 ✧",
            bg="#eef6ff",
            fg="#638de5",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack(side="right")

        key_card = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1, padx=12, pady=10)
        key_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            key_card,
            text="DeepSeek API Key",
            bg="#ffffff",
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")
        key_row = tk.Frame(key_card, bg="#ffffff")
        key_row.pack(fill="x", pady=(5, 0))
        stored_ai_key = get_setting(self.user_id, "deepseek_api_key", "")
        api_key_var = tk.StringVar(
            value=os.getenv("DEEPSEEK_API_KEY")
            or get_secret(ai_key("desktop", self.user_id), stored_ai_key)
        )
        api_key_entry = tk.Entry(
            key_row,
            textvariable=api_key_var,
            show="*",
            bg="#f8fbff",
            fg=THEME["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["line"],
            highlightcolor="#9ebcff",
            font=("Microsoft YaHei UI", 9),
        )
        api_key_entry.pack(side="left", fill="x", expand=True, ipady=5)

        def save_ai_key():
            key = api_key_var.get().strip()
            if not key:
                messagebox.showwarning("缺少 API Key", "请先填写 DeepSeek API Key。", parent=window)
                return
            stored_in_keyring = set_secret(ai_key("desktop", self.user_id), key)
            set_setting(self.user_id, "deepseek_api_key", "" if stored_in_keyring else key)
            messagebox.showinfo(
                "保存成功",
                "AI API Key 已保存到系统凭据库。" if stored_in_keyring else "系统凭据库不可用，已保存到本地数据库。",
                parent=window,
            )

        def clear_ai_key():
            delete_secret(ai_key("desktop", self.user_id))
            set_setting(self.user_id, "deepseek_api_key", "")
            api_key_var.set("")
            messagebox.showinfo("API Key 已清除", "已清除 DeepSeek API Key。", parent=window)

        self._small_action_button(key_row, "保存Key", save_ai_key).pack(side="left", padx=(8, 0))
        self._small_action_button(key_row, "清除", clear_ai_key).pack(side="left", padx=(6, 0))
        tk.Label(
            key_card,
            text="隐私提示：课程和任务摘要会发送给 DeepSeek，Cookie 不会发送。",
            bg="#ffffff",
            fg=THEME["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(6, 0))

        chat_card = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1, padx=12, pady=12)
        chat_card.pack(fill="both", expand=True)
        chat_text = tk.Text(
            chat_card,
            height=14,
            wrap="word",
            bg="#fbfdff",
            fg=THEME["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["line"],
            font=("Microsoft YaHei UI", 10),
            state="disabled",
        )
        chat_text.pack(fill="both", expand=True)
        history = []

        def append_chat(role, message):
            chat_text.configure(state="normal")
            prefix = "小课" if role == "assistant" else "我"
            color_tag = "assistant" if role == "assistant" else "user"
            chat_text.insert("end", f"{prefix}：\n", color_tag)
            chat_text.insert("end", f"{message}\n\n")
            chat_text.configure(state="disabled")
            chat_text.see("end")

        chat_text.tag_configure("assistant", foreground=THEME["blue"], font=("Microsoft YaHei UI", 10, "bold"))
        chat_text.tag_configure("user", foreground=THEME["purple"], font=("Microsoft YaHei UI", 10, "bold"))
        append_chat("assistant", INTRO_MESSAGE)

        input_row = tk.Frame(shell, bg=THEME["bg"])
        input_row.pack(side="bottom", fill="x", pady=(10, 0))
        input_box = tk.Frame(input_row, bg=THEME["bg"])
        input_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            input_box,
            text="在这里和小课对话，按 Ctrl+Enter 发送",
            bg=THEME["bg"],
            fg="#6a87c5",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(0, 4))
        user_input = tk.Text(
            input_box,
            height=3,
            wrap="word",
            bg="#ffffff",
            fg=THEME["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["line"],
            highlightcolor="#9ebcff",
            font=("Microsoft YaHei UI", 10),
        )
        user_input.pack(side="left", fill="x", expand=True)
        user_input.focus_set()

        buttons = tk.Frame(input_row, bg=THEME["bg"])
        buttons.pack(side="left", padx=(8, 0), fill="y")

        def set_busy(is_busy):
            state = "disabled" if is_busy else "normal"
            send_btn.configure(state=state)
            plan_btn.configure(state=state)

        def run_ai(kind, text):
            api_key = api_key_var.get().strip()
            if not api_key:
                append_chat("assistant", "我还没有 DeepSeek API Key。请先在上方填写并保存 Key，再让我帮你规划。")
                return
            stored_in_keyring = set_secret(ai_key("desktop", self.user_id), api_key)
            set_setting(self.user_id, "deepseek_api_key", "" if stored_in_keyring else api_key)
            courses, assignments = self.load_ai_learning_data()
            recent_history = history[-8:]
            set_busy(True)
            append_chat("assistant", "我正在读取你的课程和任务数据，稍等我整理一下。")

            def worker():
                try:
                    agent = LearningAgent(api_key=api_key)
                    if kind == "plan":
                        reply = agent.generate_today_plan(courses, assignments, recent_history)
                    else:
                        reply = agent.reply(text, courses, assignments, recent_history)
                except LLMError as exc:
                    reply = f"AI 调用失败：{exc}"
                except Exception as exc:
                    reply = f"AI 助手发生未知错误：{exc}"

                def done():
                    append_chat("assistant", reply)
                    if kind == "chat":
                        history.append({"role": "user", "content": text})
                    else:
                        history.append({"role": "user", "content": "帮我生成今日学习计划。"})
                    history.append({"role": "assistant", "content": reply})
                    set_busy(False)

                self.root.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def send_message():
            text = user_input.get("1.0", "end").strip()
            if not text:
                return
            user_input.delete("1.0", "end")
            append_chat("user", text)
            run_ai("chat", text)

        def send_by_shortcut(_event):
            send_message()
            return "break"

        def generate_plan():
            append_chat("user", "帮我生成今日学习计划。")
            run_ai("plan", "")

        send_btn = self._pill_button(buttons, "发送", send_message, THEME["blue"])
        send_btn.pack(fill="x", pady=(0, 8))
        plan_btn = self._pill_button(buttons, "今日计划", generate_plan, THEME["orange"])
        plan_btn.pack(fill="x")
        user_input.bind("<Control-Return>", send_by_shortcut)

    def load_ai_learning_data(self):
        reminder_days = max(1, self.reminder_days.get())
        with connect_db() as conn:
            course_rows = conn.execute(
                """
                SELECT * FROM courses
                WHERE user_id = ? AND status NOT IN ('已完成', '已结束')
                """,
                (self.user_id,),
            ).fetchall()
            assignment_rows = conn.execute(
                """
                SELECT * FROM assignment_tasks
                WHERE user_id = ? AND status != ?
                """,
                (self.user_id, ASSIGNMENT_COMPLETED_STATUS),
            ).fetchall()

        courses = []
        for row in sorted(course_rows, key=lambda item: nearest_time_sort_key(item, ("deadline_time", "exam_time"))):
            item = dict(row)
            if row["status"] not in {"已完成", "已结束"}:
                item["status"] = calculated_course_status(row, reminder_days)
            courses.append(item)

        assignments = []
        for row in sorted(assignment_rows, key=lambda item: nearest_time_sort_key(item, ("deadline_time",))):
            item = dict(row)
            if row["status"] != ASSIGNMENT_COMPLETED_STATUS:
                item["status"] = calculated_assignment_status(row, reminder_days)
            assignments.append(item)

        return courses, assignments

    def save_reminder_settings(self, window=None):
        days = min(30, max(1, self.reminder_days.get()))
        self.reminder_days.set(days)
        set_setting(self.user_id, "reminder_days", days)
        set_bool_setting(self.user_id, "auto_start_enabled", self.auto_start_enabled.get())
        set_bool_setting(self.user_id, "startup_auto_sync_enabled", self.startup_auto_sync_enabled.get())
        try:
            set_windows_auto_start(self.auto_start_enabled.get())
        except OSError as exc:
            messagebox.showwarning("开机自启动设置失败", str(exc), parent=window or self.root)
        except Exception as exc:
            messagebox.showwarning("开机自启动设置失败", f"写入 Windows 启动项失败：{exc}", parent=window or self.root)
        self.refresh_tip()
        self.refresh_auto_start_button()
        messagebox.showinfo(
            "提醒设置",
            f"已保存：提前 {days} 天提醒。\n"
            f"开机自启动：{'开启' if self.auto_start_enabled.get() else '关闭'}\n"
            f"启动自动同步：{'开启' if self.startup_auto_sync_enabled.get() else '关闭'}",
            parent=window or self.root,
        )

    def build_reminders(self):
        reminder_days = max(1, self.reminder_days.get())
        reminders = []
        with connect_db() as conn:
            self.mark_expired_assignments(conn)
            conn.commit()
            rows = conn.execute(
                """
                SELECT * FROM courses
                WHERE user_id = ? AND status NOT IN ('已完成', '已结束')
                ORDER BY deadline_time IS NULL, deadline_time ASC
                """,
                (self.user_id,),
            ).fetchall()
            assignment_rows = conn.execute(
                """
                SELECT * FROM assignment_tasks
                WHERE user_id = ? AND status != ?
                ORDER BY deadline_time IS NULL, deadline_time ASC
                """,
                (self.user_id, ASSIGNMENT_COMPLETED_STATUS),
            ).fetchall()

        for row in rows:
            prefix = f"[{row['platform_name']}] {row['course_name']}"
            deadline = parse_time(row["deadline_time"])
            deadline_state = reminder_state(deadline, reminder_days)
            if deadline_state:
                if deadline_state["state"] == "expired":
                    reminders.append(f"已逾期：{prefix}，截止 {row['deadline_time']}")
                else:
                    reminders.append(
                        f"即将截止：{prefix}，还剩 {deadline_state['days_left']} 天，截止 {row['deadline_time']}"
                    )

            exam_time = parse_time(row["exam_time"])
            exam_state = reminder_state(exam_time, reminder_days)
            if exam_state:
                if exam_state["state"] == "expired":
                    reminders.append(f"考试已逾期：{prefix}，考试 {row['exam_time']}")
                else:
                    reminders.append(f"即将考试：{prefix}，还剩 {exam_state['days_left']} 天，考试 {row['exam_time']}")

        for row in assignment_rows:
            prefix = f"[{row['platform_name']}] {row['course_name']}：{row['task_type']} {row['task_title']}"
            deadline = parse_time(row["deadline_time"])
            deadline_state = reminder_state(deadline, reminder_days)
            if not deadline_state:
                continue
            if deadline_state["state"] == "expired":
                reminders.append(f"作业已逾期：{prefix}，截止 {row['deadline_time']}")
            else:
                reminders.append(
                    f"作业即将截止：{prefix}，还剩 {deadline_state['days_left']} 天，截止 {row['deadline_time']}"
                )
        return reminders

    def refresh_tip(self):
        reminders = self.build_reminders()
        if hasattr(self, "tip_var"):
            self.tip_var.set(f"{len(reminders)} 个临期提醒" if reminders else "暂无临期任务")

    def show_startup_reminders(self):
        reminders = self.build_reminders()
        self.refresh_tip()
        if reminders:
            self._show_reminder_window(reminders[:8])

    def show_reminders(self):
        reminders = self.build_reminders()
        self.refresh_tip()
        self._show_reminder_window(reminders[:10])

    def _show_reminder_window(self, reminders):
        window = tk.Toplevel(self.root)
        window.title("即将截止")
        window.geometry(self._center_geometry(520, 360))
        window.configure(bg=THEME["bg"])
        window.transient(self.root)
        window.attributes("-topmost", True)

        shell = tk.Frame(window, bg=THEME["bg"], padx=16, pady=14)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=THEME["bg"])
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header,
            text="🔔  网课任务提醒",
            bg=THEME["bg"],
            fg=THEME["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text=f"{len(reminders)} 个临期提醒" if reminders else "暂无临期任务",
            bg="#eaf2ff",
            fg=THEME["blue"],
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=12,
            pady=4,
        ).pack(side="right")

        card = tk.Frame(shell, bg="#ffffff", highlightbackground=THEME["line"], highlightthickness=1, padx=14, pady=12)
        card.pack(fill="both", expand=True)

        if reminders:
            canvas = tk.Canvas(card, bg="#ffffff", highlightthickness=0)
            scrollbar = ttk.Scrollbar(card, orient="vertical", command=canvas.yview)
            list_frame = tk.Frame(canvas, bg="#ffffff")
            list_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=list_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            for reminder in reminders:
                color = THEME["red"] if "逾期" in reminder else THEME["orange"]
                item = tk.Frame(list_frame, bg="#fbfdff", highlightbackground=THEME["line"], highlightthickness=1, padx=10, pady=8)
                item.pack(fill="x", pady=(0, 8))
                tk.Label(item, text="⚠", bg="#fbfdff", fg=color, font=("Microsoft YaHei UI", 13, "bold")).pack(
                    side="left", padx=(0, 8)
                )
                tk.Label(
                    item,
                    text=reminder,
                    bg="#fbfdff",
                    fg=THEME["text"],
                    font=("Microsoft YaHei UI", 9),
                    wraplength=410,
                    justify="left",
                ).pack(side="left", fill="x", expand=True)
        else:
            pet_holder = tk.Frame(card, bg="#ffffff")
            pet_holder.pack(fill="x", pady=(8, 2))
            self._place_pet_image_or_draw(pet_holder, 160, 90)
            tk.Label(
                card,
                text="暂无临期任务，今天也很稳。",
                bg="#ffffff",
                fg="#6a87c5",
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(pady=(6, 0))

        actions = tk.Frame(shell, bg=THEME["bg"])
        actions.pack(fill="x", pady=(10, 0))
        self._pill_button(actions, "确定", window.destroy, THEME["blue"]).pack(side="right")


def main():
    root = tk.Tk()
    MiniReminderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
