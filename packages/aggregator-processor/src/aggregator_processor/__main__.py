import argparse
import sys

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging
from aggregator_processor.config import ProcessorSettings


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Article processor service")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch then exit (default: run as daemon)",
    )
    args = parser.parse_args()

    settings = ProcessorSettings()
    configure_logging(settings, stream=sys.stdout)

    if args.once:
        from aggregator_processor.loop import run_once

        run_once(settings)
    else:
        from aggregator_processor.loop import run

        run(settings)


if __name__ == "__main__":
    main()
