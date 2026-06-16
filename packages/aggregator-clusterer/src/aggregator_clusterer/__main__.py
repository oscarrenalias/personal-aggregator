import argparse
import sys

from aggregator_common import load_env
from aggregator_common.llm_telemetry import setup_llm_telemetry
from aggregator_common.logging_setup import configure_logging
from aggregator_clusterer.config import ClustererSettings


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Clusterer service — groups articles into story threads")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one clustering pass then exit (default: run as daemon)",
    )
    args = parser.parse_args()

    settings = ClustererSettings()
    configure_logging(settings, stream=sys.stdout)
    setup_llm_telemetry(settings)

    if args.once:
        from aggregator_clusterer.worker import run_once

        run_once(settings)
    else:
        from aggregator_clusterer.worker import run

        run(settings)


if __name__ == "__main__":
    main()
