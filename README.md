# GitHub Repo Recommender (MCP)

A local [MCP](https://modelcontextprotocol.io) server that helps Claude recommend specific GitHub issues and repos worth contributing to, based on *your* stack and interests rather than a generic trending list. It runs on your machine and plugs into Claude Desktop.

Ask Claude *"what should I look at this week?"* and it searches GitHub with these tools, reads the results, and picks a shortlist, each with a one-line reason it fits you. Claude does the ranking itself in the conversation; the server supplies the data.

## What it does

| Tool | What it does |
| --- | --- |
| `search_issues` | Finds open issues by label, language, and keyword, across all of GitHub or limited to your seed repos. Skips issues you've already been recommended. |
| `search_repos` | Discovers active repos by GitHub topic and/or keyword, for niche domains where issue search is noisy. Runs one query per topic, drops archived repos, forks and stale repos, and ranks by a blend of recency and stars. Supports paging, and flags repos you've already seen or shortlisted. |
| `get_repo_health` | Estimates whether a PR from an outside contributor will get a response, from recent PR outcomes: merge rate, median days to merge or close, how old the oldest open outside PR is, and when the last outside PR was merged. Results are saved as snapshots so you can compare over time. |
| `get_repo_context` | Returns a repo's description, topics, stars, language, README excerpt, and last commit date. |
| `get_issue_activity` | Estimates whether a maintainer will respond: time to first maintainer comment, days since the last one, and the repo's open vs closed issue counts. |
| `record_recommendation` | Logs an issue as recommended so it doesn't come back in later searches. |
| `update_interests` | Updates your saved profile (stack, interests, goals) when your priorities change. |
| `save_shortlist` | Saves a ranked shortlist of repos for a topic, each with a reason and a status (shortlisted, interested, dismissed, pursued). |
| `get_shortlists` | Retrieves saved shortlists with each repo's latest health snapshot, so research can be refreshed instead of redone. |

Your profile, recommendation history, repos seen, health snapshots and shortlists are stored in a local DuckDB file (`state.duckdb`). The profile starts from the blurb in your `config.yaml` and is then updated through Claude via `update_interests`. It is also exposed to Claude as the `profile://interests` resource.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/infinitevoid1793/repo-recommender-mcp.git
cd repo-recommender-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a GitHub token (read-only, dedicated to this tool)

1. On GitHub: **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Generate a new token and name it something identifiable (e.g. `repo-recommender-mcp`)
3. Set an expiration (90 days is a reasonable default; renew when it lapses)
4. Under **Repository access**, choose **Public repositories (read-only)**
5. Under **Permissions**, grant read-only access to **Issues** and **Metadata** only
6. Generate and copy the token immediately; GitHub shows it once

### 3. Configure

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

- `config.yaml`: your seed repos (optional) and an initial profile blurb (stack, interests, goals).
- `.env`: your token, as `GITHUB_TOKEN=...`.

Both files are gitignored and never committed.

### 4. Point Claude Desktop at it

Open Claude Desktop's config (**Settings → Developer → Edit Config**) and add an entry under `mcpServers`:

```json
{
  "mcpServers": {
    "repo-recommender": {
      "command": "/absolute/path/to/repo-recommender-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/repo-recommender-mcp/server.py"]
    }
  }
}
```

Use absolute paths for both. The token isn't needed here; the server loads it from `.env`. To print your paths:

```bash
echo "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
```

### 5. Restart Claude Desktop

Quit and reopen the app fully, then check the tools icon in the chat input. You should see nine tools: `search_issues`, `search_repos`, `get_repo_context`, `get_issue_activity`, `get_repo_health`, `record_recommendation`, `update_interests`, `save_shortlist`, `get_shortlists`.

## Using it

In a new chat, ask something like *"what should I look at this week?"* For a niche domain, Claude can run a research pass: check `get_shortlists` for the topic, `search_repos` to find active repos, `get_repo_health` on the candidates, then `save_shortlist` with the best ~10. Later, ask it to refresh a saved list. It can also call `search_issues` scoped to shortlisted repos to find concrete issues. It uses `get_repo_context` and `get_issue_activity` on promising candidates and scores fit against your saved profile. Ask it to call `record_recommendation` on what it surfaces so those issues don't return, and tell it your priorities have changed to have it call `update_interests`.

## Notes

- GitHub's search endpoint is rate-limited (about 30 requests per minute, even with a token), so this isn't built for high-frequency polling. `search_repos` pauses between its per-topic queries for this reason.
- Delete `state.duckdb` to start fresh (this also erases saved shortlists and health history); it is recreated and reseeded from `config.yaml` on the next run.
- Changes to `config.yaml`, `.env`, or the code take effect after restarting Claude Desktop.
- Version history is in `CHANGELOG.md`.

## License

MIT. See `LICENSE`.
