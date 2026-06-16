"""Verify that the FastMCP app has exactly the right tools, resources, and prompts."""
import asyncio


EXPECTED_TOOLS = {
    # Original read tools
    "search_articles",
    "list_articles",
    "get_article",
    "get_interest_profile",
    "list_categories",
    "list_sources",
    # Original write tools
    "mark_read",
    "mark_unread",
    "save_article",
    "unsave_article",
    "get_daily_brief",
    "refresh_brief",
    # Profile management
    "set_interest_profile",
    # Thread tools
    "list_threads",
    "get_thread",
    "dismiss_thread",
    "recluster",
    # Source management
    "add_source",
    "enable_source",
    "disable_source",
    "set_source_interval",
    "refresh_source_now",
    "remove_source",
    # Category management
    "add_category",
    "rename_category",
    "set_category_description",
    "set_category_order",
    "enable_category",
    "disable_category",
    "remove_category",
    # Ops diagnostic
    "pipeline_status",
    "list_stuck",
    "list_failures",
    # Ops remediation
    "reap_stale_claims",
    "retry_failed",
    "rerank",
    # LLM telemetry
    "llm_stats",
}

EXPECTED_RESOURCE_TEMPLATES = {"article://{id}", "feed://{view}", "thread://{id}"}
EXPECTED_STATIC_RESOURCES = {"profile://interests", "brief://today", "status://pipeline", "status://llm"}
EXPECTED_PROMPTS = {"whats_latest", "daily_brief", "troubleshoot", "whats_developing"}


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
