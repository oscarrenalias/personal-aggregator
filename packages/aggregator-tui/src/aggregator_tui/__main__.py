from __future__ import annotations

import argparse
import os

from aggregator_tui.app import AggregatorApp

_DEFAULT_API_URL = "http://localhost:8000/api/v1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregator TUI reader")
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "Base URL for the aggregator JSON API "
            "(default: AGGREGATOR_API_URL env var or http://localhost:8000/api/v1)"
        ),
    )
    args = parser.parse_args()
    api_url = args.api_url or os.environ.get("AGGREGATOR_API_URL") or _DEFAULT_API_URL
    AggregatorApp(api_url=api_url).run()


if __name__ == "__main__":
    main()
