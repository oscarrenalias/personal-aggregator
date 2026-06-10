import logging
import sys

from aggregator_common import load_env
from aggregator_common.config import Settings
from aggregator_common.logging_setup import configure_logging
from aggregator_common.version import version

logger = logging.getLogger(__name__)


def main() -> None:
    load_env()
    settings = Settings()
    configure_logging(settings, stream=sys.stdout)
    logger.info("aggregator-processor starting, version=%s", version())


if __name__ == "__main__":
    main()
