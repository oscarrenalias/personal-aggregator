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
    # FastMCP.run() only accepts (transport, mount_path); host/port/path are
    # configured on the server's settings object, not passed as run() kwargs.
    mcp.settings.host = settings.mcp_host
    mcp.settings.port = settings.mcp_port
    mcp.settings.streamable_http_path = settings.mcp_path
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
