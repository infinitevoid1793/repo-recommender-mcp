"""Everything that persists: recommendation history, the interest profile, and
saved shortlists."""

# pydantic, which builds the tool input schemas, requires
# typing_extensions.TypedDict rather than typing.TypedDict on Python < 3.12,
# and the field qualifiers must come from the same module as the class.
from typing_extensions import NotRequired, TypedDict  # noqa: UP035

from .. import db
from ..app import mcp


class ShortlistEntry(TypedDict):
    full_name: str
    reason: str
    status: NotRequired[str]


def normalize_shortlist_entries(repos: list[ShortlistEntry]) -> list[dict]:
    """Validate statuses and reject duplicates. Raises ValueError."""
    entries = []
    for r in repos:
        status = r.get("status", "shortlisted")
        if status not in db.SHORTLIST_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Use one of {sorted(db.SHORTLIST_STATUSES)}."
            )
        entries.append(
            {"full_name": r["full_name"], "reason": r.get("reason"), "status": status}
        )
    if len({e["full_name"] for e in entries}) != len(entries):
        raise ValueError("Duplicate full_name in repos.")
    return entries


@mcp.tool()
def record_recommendation(repo: str, issue_number: int, url: str, reason: str) -> dict:
    """Log an issue as recommended so it's excluded from future search_issues
    results. Call this right after presenting a recommendation to the user."""
    db.record_recommendation(repo, issue_number, url, reason)
    return {"status": "recorded", "repo": repo, "issue_number": issue_number}


@mcp.tool()
def update_interests(notes: str) -> dict:
    """Update the persisted interest profile (stack, interests, goals). Call
    this when the user tells you their priorities have changed."""
    db.update_interests(notes)
    return {"status": "updated"}


@mcp.tool()
def save_shortlist(
    topic: str,
    repos: list[ShortlistEntry],
    shortlist_id: int | None = None,
    notes: str | None = None,
) -> dict:
    """Save a ranked shortlist of repos for a topic (rank = order given).
    Each entry: full_name, a one-line reason, and optional status (shortlisted
    [default], interested, dismissed, pursued). To re-rank or change statuses,
    read the list with get_shortlists, edit it, and call this again with its
    shortlist_id, which replaces that list's entries.
    """
    try:
        entries = normalize_shortlist_entries(repos)
        saved_id = db.save_shortlist(topic, entries, shortlist_id, notes)
    except ValueError as e:
        return {"error": str(e)}
    return {"shortlist_id": saved_id, "topic": topic, "repo_count": len(entries)}


@mcp.tool()
def get_shortlists(topic: str | None = None, limit: int = 5) -> list[dict]:
    """Retrieve saved shortlists, most recently updated first, optionally
    filtered by topic (case-insensitive substring). Each repo includes its rank,
    reason, status, and latest saved health snapshot. Check this before
    starting new research so a topic can be refreshed instead of redone.
    """
    return db.get_shortlists(topic, limit)


@mcp.resource("profile://interests")
def get_interests_resource() -> str:
    """The current persisted interest profile, used to score fit."""
    return db.get_interests()
