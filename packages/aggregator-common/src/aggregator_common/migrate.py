from pathlib import Path

from alembic import command
from alembic.config import Config

from aggregator_common.config import Settings
from aggregator_common.env import load_env


def _get_url(settings: Settings) -> str:
    url = settings.database_url
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def main() -> None:
    load_env()
    settings = Settings()

    migrations_dir = Path(__file__).parent / "migrations"

    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", _get_url(settings))

    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
