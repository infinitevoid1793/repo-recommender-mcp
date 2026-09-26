# Changelog

Version history for this project.

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
