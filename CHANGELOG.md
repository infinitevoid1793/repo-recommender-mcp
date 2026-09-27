# Changelog

Version history for this project.

## v1.0 — 2026-09-26

Restructured into a proper Python package with a test suite. No change to what
any tool returns, with one bug fix noted below.

- `src/` layout: code moves to `src/repo_recommender/`, with tools split by concern into `tools/discovery.py` (search), `tools/inspection.py` (judging one candidate) and `tools/state.py` (persistence)
- `pyproject.toml` replaces `requirements.txt`, and installs a `repo-recommender` console script — Claude Desktop now points at that instead of a script path
- The `MCPServer` instance lives in `app.py`, so tool modules register against it without a circular import
- Config and the GitHub client are built in `main()` via `runtime.py` instead of at import time, so the tools can be imported without a config file or token. That is what lets the tests run in CI
- `config.yaml`, `.env` and `state.duckdb` still resolve to the project root via `paths.py`, so existing state is untouched; `REPO_RECOMMENDER_HOME` overrides it
- 88 tests, all offline: the pure logic (query building, repo ranking, health summaries, file slicing, feasibility flags, shortlist validation) plus database round-trips against a temp DuckDB file per test, and a smoke test that all twelve tools register
- Bug fix, found by a test: `search_issues(seed_repos=[])` fell back to the configured seed list instead of searching all of GitHub, because an empty list is falsy. Passing an empty list now does what the docstring always promised
- GitHub Actions CI: lint and the test suite on Python 3.11, 3.12 and 3.13, then a build job that produces the wheel and sdist, checks their filenames match the declared version, and uploads them as run artifacts. No deployment
- `timeutil.parse_time` drops a manual `Z` replacement that Python 3.11+ `fromisoformat` handles natively; `ruff` lint rules are pinned in `pyproject.toml` so an upgrade can't fail the build on newly added rules

## v0.5 — 2026-09-26

- Add `get_pr_feasibility`: finds PRs already open against an issue before time goes into vetting it or drafting a contribution spec. Returns each PR's number, title, url, author, `author_association`, `is_bot`, `created_at`, `state` (open/closed/merged) and `draft`, plus `time_to_first_pr_hours`, `contested` (an open non-draft PR exists) and `being_raced` (2+ open PRs including drafts)
- Also returns `pr_counts` (`open_ready`, `open_draft`, `merged`, `closed_unmerged`), so a merged or rejected PR isn't hidden behind `contested`. A merged PR can coexist with an open issue when the PR targets a non-default branch, omits a closing keyword, or the issue was reopened — confirmed in testing on an issue with 3 merged PRs still open
- `is_bot` is a heuristic: a login ending in `[bot]`, or an AI-authorship marker in the first 1000 characters of the PR body. Flagged as such in the output via `is_bot_is_heuristic`
- PRs are found by searching the issue number in PR titles and bodies. An issue timeline lookup was implemented first and then removed: across 7 test issues, including ones GitHub reports as `linked:pr`, `/issues/{n}/timeline` returned no `cross-referenced` or `connected` events (the legacy `mockingbird-preview` Accept header made no difference), while the text search found every PR. A PR linked only through GitHub's Development sidebar, with no mention in its text, is therefore not found
- Not cached, no new tables

## v0.4 — 2026-09-27

- Add `get_issue_detail`: full untruncated issue body plus the comment thread (author, `author_association`, body, timestamp), capped at 100 comments by default with `total_comments` and `truncated`; flags pull requests with `is_pull_request`
- Add `get_file_content`: reads a file at an optional `ref` and line range, at most 500 lines per call with `total_lines` and `truncated` for paging; if the path is a directory it returns the entries instead; files over 1 MB and binary files return an error
- Neither tool writes to the database
- `search_issues` description now points at the new tools for vetting an issue

## v0.3 — 2026-09-27

- Add `get_repo_health`: samples recent PRs (up to 3 pages, stopping at ~30 from non-maintainers, bots excluded) and returns outside-PR merge rate, median days to merge/close, oldest open outside PR, last outside merge date, and a `low_confidence` flag under 10 outside PRs. No composite score. Each result is saved as a snapshot; the previous one is returned for comparison
- Add `save_shortlist` and `get_shortlists`: ranked per-topic shortlists with a reason and status (`shortlisted`, `interested`, `dismissed`, `pursued`) per repo. Re-saving with a `shortlist_id` replaces that list's entries. `get_shortlists` includes each repo's latest health snapshot
- `search_repos`: add `page`; results carry `previously_seen` and `shortlisted` flags and every returned repo is saved
- New tables: `repos_seen`, `repo_health_snapshots`, `shortlists`, `shortlist_repos` (created automatically on startup; existing data is untouched)
- Tool descriptions include the suggested research workflow

## v0.2 — 2026-09-26

- Add `search_repos`: one `/search/repositories` query per topic (max 5, ~2s apart), merged and deduped; excludes archived repos, forks, and repos inactive for 12 months; ranked by equal-weight recency and log-scaled stars
- Multiple `languages` need no special handling: GitHub ORs repeated `language:` qualifiers

## v0.1 — 2026-09-26

Baseline.

- MCP server (`server.py`) with five tools: `search_issues`, `get_repo_context`, `get_issue_activity`, `record_recommendation`, `update_interests`
- Interest profile also exposed as the `profile://interests` MCP resource
- DuckDB state (`state.duckdb`): `recommendations` and `interests` tables; profile seeded from `config.yaml` on first run
- GitHub token loaded from `.env` (`GITHUB_TOKEN`); seed repos and initial profile from `config.yaml`
- Targets `mcp>=2` (`MCPServer` import path; `FastMCP` was renamed in 2.x)
- DuckDB connections are opened per call and closed immediately, with a short retry on lock conflicts, because Claude Desktop runs multiple server copies and DuckDB allows one read-write process per file
