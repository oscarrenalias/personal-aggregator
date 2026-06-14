import os


def version() -> str:
    return os.environ.get("AGGREGATOR_VERSION", "dev")
