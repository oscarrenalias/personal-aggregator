import sys

import uvicorn

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging
from aggregator_web.config import WebSettings


def main() -> None:
    load_env()
    settings = WebSettings()
    configure_logging(settings, stream=sys.stdout)
    uvicorn.run(
        "aggregator_web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
