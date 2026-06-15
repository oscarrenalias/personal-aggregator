import sys

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging

from aggregator_janitor.config import JanitorSettings


def main() -> None:
    load_env()
    settings = JanitorSettings()
    configure_logging(settings, stream=sys.stdout)

    from aggregator_janitor.janitor import run

    run(settings)


if __name__ == "__main__":
    main()
