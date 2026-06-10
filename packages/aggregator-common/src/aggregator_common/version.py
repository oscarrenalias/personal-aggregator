import os


def version() -> str:
    return os.environ.get("APP_VERSION", "dev")
