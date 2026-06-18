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
    parser.add_argument(
        "--cf-access-id",
        default=None,
        help=(
            "Cloudflare Access service-token Client ID, sent as the "
            "CF-Access-Client-Id header (default: CF_ACCESS_CLIENT_ID env var)"
        ),
    )
    parser.add_argument(
        "--cf-access-secret",
        default=None,
        help=(
            "Cloudflare Access service-token Client Secret, sent as the "
            "CF-Access-Client-Secret header (default: CF_ACCESS_CLIENT_SECRET env var)"
        ),
    )
    args = parser.parse_args()
    api_url = args.api_url or os.environ.get("AGGREGATOR_API_URL") or _DEFAULT_API_URL
    cf_access_client_id = args.cf_access_id or os.environ.get("CF_ACCESS_CLIENT_ID")
    cf_access_client_secret = args.cf_access_secret or os.environ.get("CF_ACCESS_CLIENT_SECRET")
    AggregatorApp(
        api_url=api_url,
        cf_access_client_id=cf_access_client_id,
        cf_access_client_secret=cf_access_client_secret,
    ).run()


if __name__ == "__main__":
    main()
