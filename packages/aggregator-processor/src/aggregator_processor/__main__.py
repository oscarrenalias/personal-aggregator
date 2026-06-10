import argparse
import logging

from aggregator_processor.config import ProcessorSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Article processor service")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch then exit (default: run as daemon)",
    )
    args = parser.parse_args()

    settings = ProcessorSettings()
    logging.basicConfig(level=settings.log_level.upper())

    if args.once:
        from aggregator_processor.loop import run_once

        run_once(settings)
    else:
        from aggregator_processor.loop import run

        run(settings)


if __name__ == "__main__":
    main()
