from __future__ import annotations

import argparse

from aggregator_tui.app import AggregatorApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregator TUI reader")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000/api/v1",
        help="Base URL for the aggregator JSON API (default: http://127.0.0.1:8000/api/v1)",
    )
    args = parser.parse_args()
    AggregatorApp(api_url=args.api_url).run()


if __name__ == "__main__":
    main()
