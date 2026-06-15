import sys

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging
from aggregator_common.config import Settings


def main() -> None:
    load_env()
    settings = Settings()
    configure_logging(settings, stream=sys.stdout)

    import logging
    log = logging.getLogger(__name__)
    log.info("aggregator-janitor starting up (stub)")


if __name__ == "__main__":
    main()
