# GitHub Repo Recommender (MCP)

A local [MCP](https://modelcontextprotocol.io) server that helps Claude recommend specific GitHub issues and repos worth contributing to, based on *your* stack and interests rather than a generic trending list. It runs on your machine and plugs into Claude Desktop.

Ask Claude *"what should I look at this week?"* and it searches GitHub with these tools, reads the results, and picks a shortlist, each with a one-line reason it fits you. Claude does the ranking itself in the conversation; the server supplies the data.

## What it does

| Tool | What it does |
| --- | --- |
| `search_issues` | Finds open issues by label, language, and keyword, across all of GitHub or limited to your seed repos. Skips issues you've already been recommended. |
| `search_repos` | Discovers active repos by GitHub topic and/or keyword, for niche domains where issue search is noisy. Runs one query per topic, drops archived repos, forks and stale repos, and ranks by a blend of recency and stars. |
| `get_repo_context` | Returns a repo's description, topics, stars, language, README excerpt, and last commit date. |
| `get_issue_activity` | Estimates whether a maintainer will respond: time to first maintainer comment, days since the last one, and the repo's open vs closed issue counts. |
| `record_recommendation` | Logs an issue as recommended so it doesn't come back in later searches. |
| `update_interests` | Updates your saved profile (stack, interests, goals) when your priorities change. |

Your profile and recommendation history are stored in a local DuckDB file (`state.duckdb`). The profile starts from the blurb in your `config.yaml` and is then updated through Claude via `update_interests`. It is also exposed to Claude as the `profile://interests` resource.

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

Quit and reopen the app fully, then check the tools icon in the chat input. You should see six tools: `search_issues`, `search_repos`, `get_repo_context`, `get_issue_activity`, `record_recommendation`, `update_interests`.

## Using it

In a new chat, ask something like *"what should I look at this week?"* For a niche domain, Claude can call `search_repos` first to find active repos by topic, then `search_issues` scoped to them. It uses `get_repo_context` and `get_issue_activity` on promising candidates and scores fit against your saved profile. Ask it to call `record_recommendation` on what it surfaces so those issues don't return, and tell it your priorities have changed to have it call `update_interests`.

## Notes

- GitHub's search endpoint is rate-limited (about 30 requests per minute, even with a token), so this isn't built for high-frequency polling. `search_repos` pauses between its per-topic queries for this reason.
- Delete `state.duckdb` to start fresh; it is recreated and reseeded from `config.yaml` on the next run.
- Changes to `config.yaml`, `.env`, or the code take effect after restarting Claude Desktop.
- Version history is in `CHANGELOG.md`.

## License

MIT. See `LICENSE`.
