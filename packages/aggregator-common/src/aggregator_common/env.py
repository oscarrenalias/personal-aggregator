from dotenv import find_dotenv, load_dotenv


def load_env() -> str | None:
    """Load .env into os.environ (override=False so real env vars always win).

    Uses find_dotenv(usecwd=True) which walks up from the current working
    directory, so it locates the repo-root .env regardless of where the
    process was launched from.  Safe to call multiple times; no-op when no
    .env file is found.
    """
    path = find_dotenv(usecwd=True)
    if not path:
        return None
    load_dotenv(path, override=False)
    return path
