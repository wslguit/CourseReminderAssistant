try:
    import keyring
    from keyring.errors import KeyringError
except ImportError:  # pragma: no cover - fallback for minimal environments
    keyring = None

    class KeyringError(Exception):
        pass


SERVICE_NAME = "CourseReminderAssistant"


def get_secret(key, fallback=""):
    if keyring is None:
        return fallback
    try:
        value = keyring.get_password(SERVICE_NAME, key)
        if value is not None:
            return value
        if fallback:
            keyring.set_password(SERVICE_NAME, key, fallback)
        return fallback
    except (KeyringError, OSError, RuntimeError):
        return fallback


def set_secret(key, value):
    if keyring is None:
        return False
    try:
        keyring.set_password(SERVICE_NAME, key, value)
        return True
    except (KeyringError, OSError, RuntimeError):
        return False


def delete_secret(key):
    if keyring is None:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, key)
        return True
    except (KeyringError, OSError, RuntimeError):
        return False


def cookie_key(scope, user_id, platform_name):
    return f"{scope}:user:{user_id}:cookie:{platform_name}"


def ai_key(scope, user_id):
    return f"{scope}:user:{user_id}:deepseek-api-key"
