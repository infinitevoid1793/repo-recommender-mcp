"""GitHub Repo Recommender MCP server.

Exposes twelve tools over stdio, grouped as: discovery (search_issues,
search_repos), inspection (get_repo_context, get_issue_activity,
get_repo_health, get_issue_detail, get_file_content, get_pr_feasibility) and
state (record_recommendation, update_interests, save_shortlist,
get_shortlists). Claude does the fit-scoring itself, in conversation, by
reasoning over what these tools return plus the persisted interest profile.
"""

from . import db, runtime, tools  # noqa: F401  (importing tools registers them)
from .app import mcp


def main() -> None:
    cfg = runtime.init()
    db.init(cfg.profile_blurb)
    mcp.run()


if __name__ == "__main__":
    main()
