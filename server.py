"""GitHub Repo Recommender MCP server.

Exposes eleven tools over stdio: search_issues, search_repos, get_repo_context,
get_issue_activity, get_repo_health, get_issue_detail, get_file_content,
record_recommendation, update_interests, save_shortlist, and get_shortlists.
Claude does the fit-scoring itself, in conversation, by reasoning over
what these tools return plus the persisted interest profile.
"""

import base64
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import NotRequired, TypedDict

from mcp.server.mcpserver import MCPServer

import db
from config import load_config
from github_client import GitHubClient, GitHubError

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

cfg = load_config()
gh = GitHubClient(cfg.github_token)
db.init(cfg.profile_blurb)

mcp = MCPServer("repo-recommender")


def _build_query(labels, languages, seed_repos, keywords) -> str:
    parts = ["is:issue", "is:open"]
    for label in labels or []:
        parts.append(f'label:"{label}"')
    for language in languages or []:
        parts.append(f"language:{language}")
    repos = seed_repos if seed_repos else cfg.seed_repos
    for repo in repos or []:
        parts.append(f"repo:{repo}")
    if keywords:
        parts.append(keywords)
    return " ".join(parts)


@mcp.tool()
def search_issues(
    labels: list[str] | None = None,
    languages: list[str] | None = None,
    seed_repos: list[str] | None = None,
    keywords: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Find candidate open GitHub issues to contribute to.

    If seed_repos is omitted, falls back to the configured seed list; pass
    an empty list explicitly to search the open web of GitHub instead.
    Issues already logged via record_recommendation are excluded. Bodies are
    only snippets: to vet a promising issue, call get_issue_detail, then
    get_file_content on the code it names.
    """
    query = _build_query(labels, languages, seed_repos, keywords)
    try:
        raw_items = gh.search_issues(query, max_results=max_results * 2)
    except GitHubError as e:
        return [{"error": str(e)}]

    already_recommended = db.get_recommended_keys()

    results = []
    for item in raw_items:
        repo_url = item.get("repository_url", "")
        repo = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
        issue_number = item.get("number")
        if (repo, issue_number) in already_recommended:
            continue
        results.append(
            {
                "repo": repo,
                "issue_number": issue_number,
                "title": item.get("title"),
                "url": item.get("html_url"),
                "labels": [label.get("name") for label in item.get("labels", [])],
                "body_snippet": (item.get("body") or "")[:300],
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "comment_count": item.get("comments"),
            }
        )
        if len(results) >= max_results:
            break

    return results


MAX_TOPICS = 5
SEARCH_REQUEST_DELAY_SECONDS = 2
RECENCY_WEIGHT = 0.5
POPULARITY_WEIGHT = 0.5


def _score_repos(repos: list[dict], window_days: int) -> None:
    now = datetime.now(timezone.utc)
    max_log_stars = math.log10(max(r["stars"] for r in repos) + 1) or 1
    for r in repos:
        pushed = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        recency = max(0.0, 1 - (now - pushed).days / window_days)
        popularity = math.log10(r["stars"] + 1) / max_log_stars
        r["_score"] = RECENCY_WEIGHT * recency + POPULARITY_WEIGHT * popularity


@mcp.tool()
def search_repos(
    topics: list[str] | None = None,
    keywords: str | None = None,
    languages: list[str] | None = None,
    min_stars: int = 10,
    active_within_months: int = 12,
    max_results: int = 20,
    page: int = 1,
) -> list[dict]:
    """Discover active repos by GitHub topic and/or keyword, for niche domains
    (e.g. soccer analytics) where search_issues is empty or noisy. Runs one
    query per topic (max 5) and merges results, so topics are OR'd. Excludes
    archived repos, forks, and repos with no push in active_within_months.
    Ranked by a blend of recency and stars. Follow up by calling search_issues
    with the interesting repos as seed_repos to find concrete issues.

    page fetches later result pages per topic (1 = first). Each result has
    previously_seen and shortlisted flags: repos are never hidden, so use the
    flags to prioritise new finds. Every returned repo is saved to history.

    Suggested research workflow: get_shortlists for the topic (refresh instead
    of redoing) -> search_repos per topic -> get_repo_health on candidates ->
    optionally get_repo_context -> pick ~10 -> save_shortlist.
    """
    if not topics and not keywords:
        return [{"error": "Provide at least one of topics or keywords."}]

    window_days = active_within_months * 30
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()

    base = [f"stars:>={min_stars}", f"pushed:>={cutoff}", "archived:false", "fork:false"]
    base += [f"language:{lang}" for lang in languages or []]
    if keywords:
        base.append(keywords)
    queries = [" ".join([f"topic:{t}"] + base) for t in (topics or [])[:MAX_TOPICS]] or [
        " ".join(base)
    ]

    merged: dict[str, dict] = {}
    for i, query in enumerate(queries):
        if i:
            time.sleep(SEARCH_REQUEST_DELAY_SECONDS)
        try:
            items = gh.search_repositories(query, page=page)
        except GitHubError as e:
            if merged:
                break
            return [{"error": str(e)}]
        for item in items:
            merged.setdefault(
                item["full_name"],
                {
                    "full_name": item["full_name"],
                    "description": item.get("description"),
                    "topics": item.get("topics", []),
                    "stars": item.get("stargazers_count", 0),
                    "primary_language": item.get("language"),
                    "pushed_at": item.get("pushed_at"),
                    "open_issues_count": item.get("open_issues_count"),
                },
            )

    repos = list(merged.values())
    if not repos:
        return []
    _score_repos(repos, window_days)
    repos.sort(key=lambda r: r["_score"], reverse=True)
    for r in repos:
        del r["_score"]
    repos = repos[:max_results]

    seen, shortlisted = db.get_seen_and_shortlisted([r["full_name"] for r in repos])
    for r in repos:
        r["previously_seen"] = r["full_name"] in seen
        r["shortlisted"] = r["full_name"] in shortlisted
    db.record_repos_seen(repos)
    return repos


MAX_HEALTH_PAGES = 3
HEALTH_TARGET_OUTSIDE_PRS = 30
LOW_CONFIDENCE_BELOW = 10


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _days_between(start: str, end: datetime) -> float:
    return (end - _parse_time(start)).total_seconds() / 86400


def _is_outside_pr(pr: dict) -> bool:
    user = pr.get("user")
    if not user or user.get("type") == "Bot" or user.get("login", "").endswith("[bot]"):
        return False
    return pr.get("author_association") not in MAINTAINER_ASSOCIATIONS


@mcp.tool()
def get_repo_health(repo: str) -> dict:
    """Estimate whether a PR from an outside contributor to this repo will get
    a response and be merged. Samples recent pull requests (up to ~90 scanned,
    stopping at ~30 from non-maintainers, bots excluded) and returns raw
    signals with sample sizes and no composite score: merge_rate, median days
    to merge or close, oldest open outside PR age, last_outside_pr_merged_at
    (is it responsive now?), and low_confidence when fewer than 10 outside
    PRs were found. The result is saved as a snapshot; the previous snapshot
    is included for comparison.
    """
    scanned = 0
    outside: list[dict] = []
    try:
        for page in range(1, MAX_HEALTH_PAGES + 1):
            batch = gh.get_pulls(repo, page=page)
            scanned += len(batch)
            outside.extend(pr for pr in batch if _is_outside_pr(pr))
            if len(batch) < 30 or len(outside) >= HEALTH_TARGET_OUTSIDE_PRS:
                break
    except GitHubError as e:
        return {"error": str(e)}

    now = datetime.now(timezone.utc)
    merged = [pr for pr in outside if pr.get("merged_at")]
    closed = [pr for pr in outside if pr.get("state") == "closed" and not pr.get("merged_at")]
    still_open = [pr for pr in outside if pr.get("state") == "open"]

    def median_days(prs: list[dict], end_field: str) -> float | None:
        if not prs:
            return None
        return round(
            statistics.median(
                (_parse_time(pr[end_field]) - _parse_time(pr["created_at"])).total_seconds()
                / 86400
                for pr in prs
            ),
            1,
        )

    decided = len(merged) + len(closed)
    metrics = {
        "prs_scanned": scanned,
        "outside_prs_sampled": len(outside),
        "merged": len(merged),
        "closed_unmerged": len(closed),
        "open_count": len(still_open),
        "merge_rate": round(len(merged) / decided, 2) if decided else None,
        "median_days_to_merge": median_days(merged, "merged_at"),
        "median_days_to_close_unmerged": median_days(closed, "closed_at"),
        "oldest_open_days": (
            int(max(_days_between(pr["created_at"], now) for pr in still_open))
            if still_open
            else None
        ),
        "last_outside_pr_merged_at": (
            max(pr["merged_at"] for pr in merged) if merged else None
        ),
        "low_confidence": len(outside) < LOW_CONFIDENCE_BELOW,
    }

    previous = db.get_latest_health_snapshot(repo)
    db.save_health_snapshot(repo, metrics)
    return {"repo": repo, **metrics, "previous_snapshot": previous}


class ShortlistEntry(TypedDict):
    full_name: str
    reason: str
    status: NotRequired[str]


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
    entries = []
    for r in repos:
        status = r.get("status", "shortlisted")
        if status not in db.SHORTLIST_STATUSES:
            return {"error": f"Invalid status '{status}'. Use one of {sorted(db.SHORTLIST_STATUSES)}."}
        entries.append({"full_name": r["full_name"], "reason": r.get("reason"), "status": status})
    if len({e["full_name"] for e in entries}) != len(entries):
        return {"error": "Duplicate full_name in repos."}
    try:
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


MAX_ISSUE_COMMENTS = 100
MAX_FILE_LINES = 500


@mcp.tool()
def get_issue_detail(repo: str, issue_number: int, max_comments: int = MAX_ISSUE_COMMENTS) -> dict:
    """Read an issue in full: the untruncated body plus its comment thread in
    order (author, author_association, body, timestamp). Use this to vet a
    candidate from search_issues or a shortlist before recommending it, then
    call get_file_content on whatever file or function the issue names.
    Comments are capped at max_comments (default 100); total_comments and
    truncated say whether the thread was cut. Not saved to history.
    """
    try:
        issue = gh.get_issue(repo, issue_number)
        comments: list[dict] = []
        page = 1
        while len(comments) < max_comments:
            batch = gh.get_issue_comments(repo, issue_number, page=page, per_page=100)
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    except GitHubError as e:
        return {"error": str(e)}

    total = issue.get("comments", len(comments))
    comments = comments[:max_comments]
    return {
        "repo": repo,
        "issue_number": issue_number,
        "title": issue.get("title"),
        "url": issue.get("html_url"),
        "state": issue.get("state"),
        "is_pull_request": "pull_request" in issue,
        "author": (issue.get("user") or {}).get("login"),
        "author_association": issue.get("author_association"),
        "labels": [label.get("name") for label in issue.get("labels", [])],
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "body": issue.get("body") or "",
        "total_comments": total,
        "truncated": total > len(comments),
        "comments": [
            {
                "author": (c.get("user") or {}).get("login"),
                "author_association": c.get("author_association"),
                "created_at": c.get("created_at"),
                "body": c.get("body") or "",
            }
            for c in comments
        ],
    }


@mcp.tool()
def get_file_content(
    repo: str,
    path: str,
    ref: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict:
    """Read a file from a repo so an issue's claim can be checked against the
    real code. ref is a branch, tag or commit (default: the default branch).
    start_line/end_line (1-based, inclusive) return a slice; at most 500 lines
    come back per call, with total_lines and truncated so you can page with a
    later start_line. If path is a directory, returns its entries instead.
    Files over 1 MB and binary files are not returned. Not saved to history.
    """
    if start_line is not None and start_line < 1:
        return {"error": "start_line must be 1 or greater."}
    if end_line is not None and end_line < (start_line or 1):
        return {"error": "end_line must be greater than or equal to start_line."}

    try:
        data = gh.get_contents(repo, path, ref)
    except GitHubError as e:
        return {"error": str(e)}

    if isinstance(data, list):
        return {
            "repo": repo,
            "path": path,
            "type": "directory",
            "entries": [{"name": e["name"], "type": e["type"], "path": e["path"]} for e in data],
        }
    if data.get("type") != "file":
        return {"error": f"Path is a {data.get('type')}, not a readable file."}
    if data.get("encoding") != "base64" or not data.get("content"):
        return {"error": "File is too large for the contents API (over 1 MB) or empty."}
    try:
        text = base64.b64decode(data["content"]).decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "File is binary or not UTF-8 text."}

    lines = text.splitlines()
    total = len(lines)
    first = start_line or 1
    if first > total:
        return {"error": f"start_line {first} is past the end of the file ({total} lines)."}
    last = min(end_line or total, total)
    capped_last = min(last, first + MAX_FILE_LINES - 1)
    return {
        "repo": repo,
        "path": path,
        "ref": ref,
        "sha": data.get("sha"),
        "total_lines": total,
        "start_line": first,
        "end_line": capped_last,
        "truncated": capped_last < last,
        "content": "\n".join(lines[first - 1 : capped_last]),
    }


AI_AUTHORSHIP_MARKERS = ("devin", "copilot", "codex", "cursor", "claude", "generated with")
MAX_FEASIBILITY_PRS = 10


def _looks_ai_authored(login: str, body: str) -> bool:
    if login.endswith("[bot]"):
        return True
    head = (body or "")[:1000].lower()
    return any(marker in head for marker in AI_AUTHORSHIP_MARKERS)


@mcp.tool()
def get_pr_feasibility(repo: str, issue_number: int) -> dict:
    """Check whether an issue already has pull requests against it, before
    spending time vetting it or drafting a contribution spec. Issues are
    frequently raced within minutes by AI agents that link the issue only from
    the PR body, so the issue's own comment thread shows nothing. Run this
    right after get_issue_detail and before get_file_content: ruling an issue
    out here is cheaper than reading the code first.

    Finds PRs by searching for the issue number in PR titles and bodies, then
    fetches each PR's real state. contested means an open non-draft PR exists;
    being_raced means 2+ open PRs including drafts. A PR linked only through
    GitHub's Development sidebar, with no mention in its text, is not found.
    is_bot is a heuristic (bot login, or AI authorship hinted in the PR body),
    not authoritative. Not saved to history.
    """
    try:
        issue = gh.get_issue(repo, issue_number)
        found = gh.search_issues(f"repo:{repo} {issue_number} in:title,body type:pr", 30)
    except GitHubError as e:
        return {"error": str(e)}
    numbers = {item["number"] for item in found if item.get("number")}
    issue_created_at = issue.get("created_at")

    prs = []
    for number in sorted(numbers)[:MAX_FEASIBILITY_PRS]:
        try:
            pr = gh.get_pull(repo, number)
        except GitHubError:
            continue
        login = (pr.get("user") or {}).get("login", "")
        merged = bool(pr.get("merged_at"))
        prs.append(
            {
                "number": pr["number"],
                "title": pr.get("title"),
                "url": pr.get("html_url"),
                "author": login,
                "author_association": pr.get("author_association"),
                "is_bot": _looks_ai_authored(login, pr.get("body") or ""),
                "created_at": pr.get("created_at"),
                "state": "merged" if merged else pr.get("state"),
                "draft": bool(pr.get("draft")),
            }
        )

    prs.sort(key=lambda p: p["created_at"] or "")
    open_ready = [p for p in prs if p["state"] == "open" and not p["draft"]]
    open_draft = [p for p in prs if p["state"] == "open" and p["draft"]]

    time_to_first_pr_hours = None
    if prs and issue_created_at and prs[0]["created_at"]:
        delta = _parse_time(prs[0]["created_at"]) - _parse_time(issue_created_at)
        time_to_first_pr_hours = round(delta.total_seconds() / 3600, 2)

    return {
        "repo": repo,
        "issue_number": issue_number,
        "issue_created_at": issue_created_at,
        "issue_state": issue.get("state"),
        "pr_counts": {
            "open_ready": len(open_ready),
            "open_draft": len(open_draft),
            "merged": len([p for p in prs if p["state"] == "merged"]),
            "closed_unmerged": len([p for p in prs if p["state"] == "closed"]),
        },
        "contested": bool(open_ready),
        "being_raced": len(open_ready) + len(open_draft) >= 2,
        "time_to_first_pr_hours": time_to_first_pr_hours,
        "is_bot_is_heuristic": True,
        "pull_requests": prs,
    }


@mcp.tool()
def get_repo_context(repo: str) -> dict:
    """Get repo-level context to judge fit beyond an issue title: description,
    topics, stars, primary language, a README excerpt, and last commit date."""
    try:
        repo_data = gh.get_repo(repo)
        readme_excerpt = gh.get_readme_excerpt(repo)
    except GitHubError as e:
        return {"error": str(e)}

    return {
        "repo": repo,
        "description": repo_data.get("description"),
        "topics": repo_data.get("topics", []),
        "stars": repo_data.get("stargazers_count"),
        "primary_language": repo_data.get("language"),
        "readme_excerpt": readme_excerpt,
        "last_commit_date": repo_data.get("pushed_at"),
    }


@mcp.tool()
def get_issue_activity(repo: str, issue_number: int) -> dict:
    """Estimate whether a maintainer will actually respond to a given issue:
    time to first maintainer comment, open/closed issue counts for the repo,
    and days since the last maintainer comment."""
    try:
        comments = gh.get_issue_comments(repo, issue_number)
        counts = gh.get_repo_issue_counts(repo)
    except GitHubError as e:
        return {"error": str(e)}

    maintainer_comments = [
        c for c in comments if c.get("author_association") in MAINTAINER_ASSOCIATIONS
    ]

    first_maintainer_comment_at = (
        maintainer_comments[0]["created_at"] if maintainer_comments else None
    )
    last_maintainer_comment_at = (
        maintainer_comments[-1]["created_at"] if maintainer_comments else None
    )

    days_since_last_maintainer_comment = None
    if last_maintainer_comment_at:
        last_dt = datetime.fromisoformat(last_maintainer_comment_at.replace("Z", "+00:00"))
        days_since_last_maintainer_comment = (datetime.now(timezone.utc) - last_dt).days

    return {
        "repo": repo,
        "issue_number": issue_number,
        "first_maintainer_comment_at": first_maintainer_comment_at,
        "days_since_last_maintainer_comment": days_since_last_maintainer_comment,
        "maintainer_comment_count": len(maintainer_comments),
        "total_comment_count": len(comments),
        "open_issue_count": counts["open_count"],
        "closed_issue_count": counts["closed_count"],
    }


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


@mcp.resource("profile://interests")
def get_interests_resource() -> str:
    """The current persisted interest profile, used to score fit."""
    return db.get_interests()


if __name__ == "__main__":
    mcp.run()
