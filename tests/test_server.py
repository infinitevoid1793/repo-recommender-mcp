"""Registration smoke tests. These import the server without a config file or
token, which is what makes them runnable in CI."""

import asyncio

EXPECTED_TOOLS = {
    "search_issues",
    "search_repos",
    "get_repo_context",
    "get_issue_activity",
    "get_repo_health",
    "get_issue_detail",
    "get_file_content",
    "get_pr_feasibility",
    "record_recommendation",
    "update_interests",
    "save_shortlist",
    "get_shortlists",
}


def registered_tools():
    from repo_recommender import server  # noqa: F401  (registers the tools)
    from repo_recommender.app import mcp

    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def test_every_tool_is_registered():
    assert set(registered_tools()) == EXPECTED_TOOLS


def test_every_tool_has_a_description_for_claude_to_choose_from():
    for name, tool in registered_tools().items():
        assert tool.description, f"{name} has no description"


def test_the_interest_profile_is_exposed_as_a_resource():
    from repo_recommender import server  # noqa: F401
    from repo_recommender.app import mcp

    uris = {str(r.uri) for r in asyncio.run(mcp.list_resources())}
    assert "profile://interests" in uris


def test_importing_the_server_does_not_require_config_or_a_token():
    """Config and the GitHub client are built in main(), not at import time."""
    from repo_recommender import runtime

    assert runtime._cfg is None
    assert runtime._gh is None
