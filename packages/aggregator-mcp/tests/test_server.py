"""Verify that the FastMCP app has exactly the right tools, resources, and prompts."""
import asyncio


EXPECTED_TOOLS = {
    "search_articles",
    "list_articles",
    "get_article",
    "get_interest_profile",
    "list_categories",
    "list_sources",
    "mark_read",
    "mark_unread",
    "save_article",
    "unsave_article",
}

EXPECTED_RESOURCE_TEMPLATES = {"article://{id}", "feed://{view}"}
EXPECTED_STATIC_RESOURCES = {"profile://interests"}
EXPECTED_PROMPTS = {"whats_latest"}


def test_exact_tool_names_registered(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    registered = {t.name for t in tools}
    assert registered == EXPECTED_TOOLS, (
        f"Tool mismatch.\n  Expected: {sorted(EXPECTED_TOOLS)}\n  Got: {sorted(registered)}"
    )


def test_resource_templates_registered(mcp_server):
    templates = asyncio.run(mcp_server.list_resource_templates())
    uris = {t.uriTemplate for t in templates}
    for expected_uri in EXPECTED_RESOURCE_TEMPLATES:
        assert expected_uri in uris, (
            f"Resource template {expected_uri!r} not found in registered templates: {uris}"
        )


def test_static_resource_registered(mcp_server):
    resources = asyncio.run(mcp_server.list_resources())
    uris = {str(r.uri) for r in resources}
    for expected_uri in EXPECTED_STATIC_RESOURCES:
        assert expected_uri in uris, (
            f"Static resource {expected_uri!r} not found in registered resources: {uris}"
        )


def test_prompt_names_registered(mcp_server):
    prompts = asyncio.run(mcp_server.list_prompts())
    registered = {p.name for p in prompts}
    assert EXPECTED_PROMPTS <= registered, (
        f"Missing prompts: {EXPECTED_PROMPTS - registered}"
    )
