import argparse
import sys

from aggregator_common.logging_setup import configure_logging
from aggregator_summarize_rank.config import SummarizeRankSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize-rank service")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch then exit (default: run as daemon)",
    )
    args = parser.parse_args()

    settings = SummarizeRankSettings()
    configure_logging(settings, stream=sys.stdout)

    if args.once:
        from aggregator_summarize_rank.loop import run_once

        run_once(settings)
    else:
        from aggregator_summarize_rank.loop import run

        run(settings)


if __name__ == "__main__":
    main()
