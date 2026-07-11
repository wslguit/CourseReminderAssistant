import os
import shutil

from app_info import APP_NAME


def app_root():
    return os.path.abspath(os.path.dirname(__file__))


def user_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def database_path():
    path = os.path.join(user_data_dir(), "data.sqlite3")
    legacy_path = os.path.join(app_root(), "data.sqlite3")
    if not os.path.exists(path) and os.path.exists(legacy_path):
        try:
            shutil.copy2(legacy_path, path)
        except OSError:
            pass
    return path
