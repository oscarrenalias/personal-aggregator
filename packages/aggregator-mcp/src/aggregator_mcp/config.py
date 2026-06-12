from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class McpSettings(BaseSettings):
    mcp_host: str = Field("0.0.0.0", description="Host to bind the MCP server (MCP_HOST)")
    mcp_port: int = Field(8765, description="Port to bind the MCP server (MCP_PORT)")
    mcp_path: str = Field("/mcp", description="URL path for the MCP endpoint (MCP_PATH)")
    mcp_default_limit: int = Field(20, description="Default result limit for list/search tools (MCP_DEFAULT_LIMIT)")
    mcp_max_limit: int = Field(100, description="Maximum allowed result limit (MCP_MAX_LIMIT)")
