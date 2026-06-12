import sys

from mcp.server.fastmcp import FastMCP

from aggregator_common import load_env
from aggregator_common.logging_setup import configure_logging
from aggregator_mcp.config import McpSettings


def main() -> None:
    load_env()
    settings = McpSettings()
    configure_logging(settings, stream=sys.stdout)

    # server.py will register tools/resources/prompts on this instance; imported here once implemented.
    mcp = FastMCP("aggregator-mcp")
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path=settings.mcp_path,
    )


if __name__ == "__main__":
    main()
