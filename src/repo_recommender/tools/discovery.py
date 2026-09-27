"""Finding candidates: issue search and topic-based repo discovery."""

import math
import time
from datetime import UTC, datetime, timedelta

from .. import db, runtime
from ..app import mcp
from ..github_client import GitHubError
from ..timeutil import parse_time

MAX_TOPICS = 5
SEARCH_REQUEST_DELAY_SECONDS = 2
RECENCY_WEIGHT = 0.5
POPULARITY_WEIGHT = 0.5


def build_issue_query(
    labels: list[str] | None,
    languages: list[str] | None,
    seed_repos: list[str] | None,
    keywords: str | None,
    default_seed_repos: list[str] | None = None,
) -> str:
    parts = ["is:issue", "is:open"]
    for label in labels or []:
        parts.append(f'label:"{label}"')
    for language in languages or []:
        parts.append(f"language:{language}")
    # An explicit empty list means "search all of GitHub", so distinguish it
    # from the argument being omitted entirely.
    repos = seed_repos if seed_repos is not None else default_seed_repos
    for repo in repos or []:
        parts.append(f"repo:{repo}")
    if keywords:
        parts.append(keywords)
    return " ".join(parts)


def build_repo_queries(
    topics: list[str] | None,
    keywords: str | None,
    languages: list[str] | None,
    min_stars: int,
    cutoff: str,
) -> list[str]:
    base = [f"stars:>={min_stars}", f"pushed:>={cutoff}", "archived:false", "fork:false"]
    base += [f"language:{lang}" for lang in languages or []]
    if keywords:
        base.append(keywords)
    topic_queries = [" ".join([f"topic:{t}"] + base) for t in (topics or [])[:MAX_TOPICS]]
    return topic_queries or [" ".join(base)]


def rank_repos(
    repos: list[dict], window_days: int, now: datetime | None = None
) -> list[dict]:
    """Sort by an equal blend of push recency and log-scaled stars, so a few
    very popular repos don't crowd out active niche ones."""
    if not repos:
        return []
    now = now or datetime.now(UTC)
    max_log_stars = math.log10(max(r["stars"] for r in repos) + 1) or 1
    scored = []
    for r in repos:
        recency = max(0.0, 1 - (now - parse_time(r["pushed_at"])).days / window_days)
        popularity = math.log10(r["stars"] + 1) / max_log_stars
        scored.append((RECENCY_WEIGHT * recency + POPULARITY_WEIGHT * popularity, r))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [r for _, r in scored]


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
    query = build_issue_query(
        labels, languages, seed_repos, keywords, runtime.cfg().seed_repos
    )
    try:
        raw_items = runtime.gh().search_issues(query, max_results=max_results * 2)
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
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).date().isoformat()
    queries = build_repo_queries(topics, keywords, languages, min_stars, cutoff)

    merged: dict[str, dict] = {}
    for i, query in enumerate(queries):
        if i:
            time.sleep(SEARCH_REQUEST_DELAY_SECONDS)
        try:
            items = runtime.gh().search_repositories(query, page=page)
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

    repos = rank_repos(list(merged.values()), window_days)[:max_results]
    if not repos:
        return []

    seen, shortlisted = db.get_seen_and_shortlisted([r["full_name"] for r in repos])
    for r in repos:
        r["previously_seen"] = r["full_name"] in seen
        r["shortlisted"] = r["full_name"] in shortlisted
    db.record_repos_seen(repos)
    return repos
