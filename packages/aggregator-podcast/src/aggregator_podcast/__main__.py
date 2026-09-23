import argparse
import sys

from aggregator_common import load_env
from aggregator_common.llm_telemetry import setup_llm_telemetry
from aggregator_common.logging_setup import configure_logging
from aggregator_podcast.config import PodcastSettings


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Podcast service — generate daily audio news briefings")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Generate one episode then exit (default: run as daemon)",
    )
    args = parser.parse_args()

    settings = PodcastSettings()
    configure_logging(settings, stream=sys.stdout)
    setup_llm_telemetry(settings)

    if args.once:
        from aggregator_podcast.loop import run_once

        run_once(settings)
    else:
        from aggregator_podcast.loop import run

        run(settings)


if __name__ == "__main__":
    main()
