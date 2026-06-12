import sys

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging
from aggregator_mcp.config import McpSettings


def main() -> None:
    load_env()
    settings = McpSettings()
    configure_logging(settings, stream=sys.stdout)

    # Deferred import: server.py imports db.py which creates the engine at module level,
    # so it must be imported after load_env() sets DATABASE_URL.
    from aggregator_mcp.server import mcp  # noqa: PLC0415
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path=settings.mcp_path,
    )


if __name__ == "__main__":
    main()
