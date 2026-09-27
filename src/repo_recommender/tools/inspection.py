"""Inspecting one candidate: repo context, responsiveness, issue text, code,
and whether an issue has already been raced."""

import base64
import statistics
from datetime import UTC, datetime

from .. import db, runtime
from ..app import mcp
from ..github_client import GitHubError
from ..timeutil import parse_time

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAX_HEALTH_PAGES = 3
HEALTH_TARGET_OUTSIDE_PRS = 30
LOW_CONFIDENCE_BELOW = 10
MAX_ISSUE_COMMENTS = 100
MAX_FILE_LINES = 500
AI_AUTHORSHIP_MARKERS = ("devin", "copilot", "codex", "cursor", "claude", "generated with")
MAX_FEASIBILITY_PRS = 10


def is_outside_pr(pr: dict) -> bool:
    user = pr.get("user")
    if not user or user.get("type") == "Bot" or user.get("login", "").endswith("[bot]"):
        return False
    return pr.get("author_association") not in MAINTAINER_ASSOCIATIONS


def looks_ai_authored(login: str, body: str) -> bool:
    """Best-effort guess, not authoritative: a bot login, or an AI-agent marker
    near the top of the PR body."""
    if login.endswith("[bot]"):
        return True
    head = (body or "")[:1000].lower()
    return any(marker in head for marker in AI_AUTHORSHIP_MARKERS)


def _median_days(prs: list[dict], end_field: str) -> float | None:
    if not prs:
        return None
    return round(
        statistics.median(
            (parse_time(pr[end_field]) - parse_time(pr["created_at"])).total_seconds() / 86400
            for pr in prs
        ),
        1,
    )


def summarize_pr_health(
    outside: list[dict], scanned: int, now: datetime | None = None
) -> dict:
    """Turn a sample of outside PRs into responsiveness signals. No composite
    score: sample sizes are reported so the caller can judge confidence."""
    now = now or datetime.now(UTC)
    merged = [pr for pr in outside if pr.get("merged_at")]
    closed = [pr for pr in outside if pr.get("state") == "closed" and not pr.get("merged_at")]
    still_open = [pr for pr in outside if pr.get("state") == "open"]
    decided = len(merged) + len(closed)
    return {
        "prs_scanned": scanned,
        "outside_prs_sampled": len(outside),
        "merged": len(merged),
        "closed_unmerged": len(closed),
        "open_count": len(still_open),
        "merge_rate": round(len(merged) / decided, 2) if decided else None,
        "median_days_to_merge": _median_days(merged, "merged_at"),
        "median_days_to_close_unmerged": _median_days(closed, "closed_at"),
        "oldest_open_days": (
            int(
                max(
                    (now - parse_time(pr["created_at"])).total_seconds() / 86400
                    for pr in still_open
                )
            )
            if still_open
            else None
        ),
        "last_outside_pr_merged_at": (max(pr["merged_at"] for pr in merged) if merged else None),
        "low_confidence": len(outside) < LOW_CONFIDENCE_BELOW,
    }


def slice_lines(text: str, start_line: int | None, end_line: int | None) -> dict:
    """Return at most MAX_FILE_LINES lines, with enough detail to page for the
    rest. Raises ValueError on a range that can't be satisfied."""
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be 1 or greater.")
    if end_line is not None and end_line < (start_line or 1):
        raise ValueError("end_line must be greater than or equal to start_line.")

    lines = text.splitlines()
    total = len(lines)
    first = start_line or 1
    if first > total:
        raise ValueError(f"start_line {first} is past the end of the file ({total} lines).")
    last = min(end_line or total, total)
    capped_last = min(last, first + MAX_FILE_LINES - 1)
    return {
        "total_lines": total,
        "start_line": first,
        "end_line": capped_last,
        "truncated": capped_last < last,
        "content": "\n".join(lines[first - 1 : capped_last]),
    }


def summarize_feasibility(prs: list[dict], issue_created_at: str | None) -> dict:
    """Counts and flags over the PRs found for an issue. Drafts are counted
    separately: several open drafts means the issue is being raced hard, while
    merged or rejected PRs would otherwise hide behind `contested`."""
    open_ready = [p for p in prs if p["state"] == "open" and not p["draft"]]
    open_draft = [p for p in prs if p["state"] == "open" and p["draft"]]

    time_to_first_pr_hours = None
    if prs and issue_created_at and prs[0]["created_at"]:
        delta = parse_time(prs[0]["created_at"]) - parse_time(issue_created_at)
        time_to_first_pr_hours = round(delta.total_seconds() / 3600, 2)

    return {
        "pr_counts": {
            "open_ready": len(open_ready),
            "open_draft": len(open_draft),
            "merged": len([p for p in prs if p["state"] == "merged"]),
            "closed_unmerged": len([p for p in prs if p["state"] == "closed"]),
        },
        "contested": bool(open_ready),
        "being_raced": len(open_ready) + len(open_draft) >= 2,
        "time_to_first_pr_hours": time_to_first_pr_hours,
    }


@mcp.tool()
def get_repo_context(repo: str) -> dict:
    """Get repo-level context to judge fit beyond an issue title: description,
    topics, stars, primary language, a README excerpt, and last commit date."""
    try:
        repo_data = runtime.gh().get_repo(repo)
        readme_excerpt = runtime.gh().get_readme_excerpt(repo)
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
        comments = runtime.gh().get_issue_comments(repo, issue_number)
        counts = runtime.gh().get_repo_issue_counts(repo)
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
        last_dt = parse_time(last_maintainer_comment_at)
        days_since_last_maintainer_comment = (datetime.now(UTC) - last_dt).days

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
            batch = runtime.gh().get_pulls(repo, page=page)
            scanned += len(batch)
            outside.extend(pr for pr in batch if is_outside_pr(pr))
            if len(batch) < 30 or len(outside) >= HEALTH_TARGET_OUTSIDE_PRS:
                break
    except GitHubError as e:
        return {"error": str(e)}

    metrics = summarize_pr_health(outside, scanned)
    previous = db.get_latest_health_snapshot(repo)
    db.save_health_snapshot(repo, metrics)
    return {"repo": repo, **metrics, "previous_snapshot": previous}


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
        issue = runtime.gh().get_issue(repo, issue_number)
        comments: list[dict] = []
        page = 1
        while len(comments) < max_comments:
            batch = runtime.gh().get_issue_comments(
                repo, issue_number, page=page, per_page=100
            )
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
    try:
        data = runtime.gh().get_contents(repo, path, ref)
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

    try:
        sliced = slice_lines(text, start_line, end_line)
    except ValueError as e:
        return {"error": str(e)}

    return {"repo": repo, "path": path, "ref": ref, "sha": data.get("sha"), **sliced}


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
        issue = runtime.gh().get_issue(repo, issue_number)
        found = runtime.gh().search_issues(
            f"repo:{repo} {issue_number} in:title,body type:pr", 30
        )
    except GitHubError as e:
        return {"error": str(e)}
    numbers = {item["number"] for item in found if item.get("number")}

    prs = []
    for number in sorted(numbers)[:MAX_FEASIBILITY_PRS]:
        try:
            pr = runtime.gh().get_pull(repo, number)
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
                "is_bot": looks_ai_authored(login, pr.get("body") or ""),
                "created_at": pr.get("created_at"),
                "state": "merged" if merged else pr.get("state"),
                "draft": bool(pr.get("draft")),
            }
        )

    prs.sort(key=lambda p: p["created_at"] or "")
    issue_created_at = issue.get("created_at")
    return {
        "repo": repo,
        "issue_number": issue_number,
        "issue_created_at": issue_created_at,
        "issue_state": issue.get("state"),
        **summarize_feasibility(prs, issue_created_at),
        "is_bot_is_heuristic": True,
        "pull_requests": prs,
    }
