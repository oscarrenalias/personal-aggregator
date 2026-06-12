"""Regression tests for the service entrypoint wiring.

The unit gate previously missed that FastMCP.run() does not accept host/port/
path kwargs — those must be applied to mcp.settings before run(). These tests
exercise main() with run() patched so a bad signature surfaces here instead of
only at container startup.
"""

import inspect
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP


def test_fastmcp_run_does_not_accept_host_port_path():
    """Guard the assumption behind the entrypoint: run() takes no host/port/path."""
    params = set(inspect.signature(FastMCP.run).parameters)
    assert "host" not in params
    assert "port" not in params
    assert "path" not in params


def test_main_applies_settings_and_runs_streamable_http(db_session_factory, monkeypatch):
    # db_session_factory ensures DATABASE_URL is set so the deferred server import works.
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("MCP_PATH", "/mcp")

    from aggregator_mcp.__main__ import main
    from aggregator_mcp.server import mcp

    with patch.object(mcp, "run") as run_mock:
        main()

    # run() invoked with transport only — no rejected kwargs.
    run_mock.assert_called_once_with(transport="streamable-http")
    assert mcp.settings.host == "0.0.0.0"
    assert mcp.settings.port == 9999
    assert mcp.settings.streamable_http_path == "/mcp"
