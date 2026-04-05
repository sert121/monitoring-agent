# monitoring-agent

A minimal Hacker News monitoring agent. Polls the [HN Algolia API](https://hn.algolia.com/api) for new stories matching your queries and streams results as JSONL to stdout. Optionally POSTs each result to a webhook.

Zero dependencies — just Python 3.7+ standard library.

## Quick start

```bash
# One-shot: show all matching stories from the last 24h
python monitor.py run "AI agents"

# Watch mode: show initial results, then poll every 5 minutes for new ones
python monitor.py run "AI agents" --watch

# Multiple monitors with labels
python monitor.py run "llm:LLM inference" "rag:RAG pipelines" --watch

# Send results to a webhook too
python monitor.py run "AI agents" --watch --webhook https://your.webhook.url
```

## Commands

### `run`

Start monitoring one or more queries.

```
python monitor.py run <query> [<query> ...] [options]
```

Queries can be plain strings or labeled as `name:query` (e.g. `llm:large language models`).

On the first run, it fetches and displays all matching results from the lookback window. In `--watch` mode, subsequent polls only show new items (deduplication via local SQLite).

| Flag | Default | Description |
|------|---------|-------------|
| `--watch` | off | Keep polling after the initial check |
| `--interval SECS` | 300 | Seconds between polls (watch mode) |
| `--hours-back N` | 24 | How far back to search (hours) |
| `--hits N` | 20 | Max results per query per check |
| `--tags TAG` | story | HN content type (`story`, `comment`, `poll`, etc.) |
| `--webhook URL` | none | POST each result as JSON to this URL |

### `search`

One-off search with no deduplication or persistence.

```
python monitor.py search <query> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--hours-back N` | 24 | How far back to search |
| `--hits N` | 10 | Max results |
| `--tags TAG` | story | HN content type |
| `--webhook URL` | none | POST results to this URL |

## Output format

Every line is a JSON object. Results look like:

```json
{"id": "12345", "title": "Some HN Story", "url": "https://example.com", "hn_url": "https://news.ycombinator.com/item?id=12345", "points": 42, "comments": 10, "author": "user", "created_at": "2025-01-01T00:00:00Z", "monitor": "AI agents"}
```

Lifecycle events have an `_event` field:

```json
{"_event": "start", "monitors": ["AI agents"], "interval": 300, "hours_back": 24, "_ts": "..."}
{"_event": "checking", "monitor": "AI agents", "query": "AI agents", "_ts": "..."}
{"_event": "check_done", "monitor": "AI agents", "new": 3, "total": 10, "_ts": "..."}
{"_event": "sleeping", "seconds": 300, "_ts": "..."}
```

## Piping examples

```bash
# Pretty-print results
python monitor.py run "AI agents" | jq .

# Filter to just results (skip events)
python monitor.py run "AI agents" | jq 'select(._event == null)'

# Save to file while still seeing output
python monitor.py run "AI agents" --watch | tee results.jsonl
```
