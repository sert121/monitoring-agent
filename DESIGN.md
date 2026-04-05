# Web Monitoring Agent — Design Doc

## Goal

Build a self-hosted agent that continuously monitors the web for new content matching user-defined topics, then surfaces relevant results with summaries and alerts. Similar to Yutori.ai / Exa Monitor, but **zero API cost** — powered entirely by free feeds, scraping, and open APIs.

---

## Core Concepts

**Monitor** — a user-defined watch. Has a query/topic, a set of sources, and delivery config (email, webhook, CLI output).

**Source** — a pluggable input that yields candidate items. Each source adapter normalizes output to a common `Item` schema: `{title, url, content, timestamp, source_type}`.

**Pipeline** — `Sources → Dedup → Relevance Filter → Store → Notify`

---

## Free Source Adapters

| Adapter | Method | Covers |
|---------|--------|--------|
| **RSS/Atom** | `feedparser` | News sites, blogs, subreddits, arXiv, GitHub releases |
| **Reddit** | JSON API (`.json` suffix, no auth) | Subreddit posts, search results, comments |
| **Hacker News** | Algolia Search API (free, no key) | HN stories, Show HN, Ask HN |
| **GitHub** | REST API (unauthenticated 60 req/hr) | Repo events, releases, trending |
| **Web Scrape** | `httpx` + `BeautifulSoup` | Any page — extract links, text, structured data |
| **Change Detection** | `httpx` + `difflib` | Detect changes on specific URLs (pricing pages, docs, etc.) |
| **Google Alerts** | Scrape the alert RSS feed | Broad keyword coverage across the web for free |
| **Common Crawl** | CC Index API | Historical / bulk discovery (batch, not real-time) |

### RSS is the backbone

Most of the web still publishes RSS. Reddit, YouTube, most news sites, blogs, GitHub — all have feeds. The agent should make RSS the primary, cheapest polling mechanism and only fall back to scraping when no feed exists.

---

## Architecture

```
┌─────────────┐
│  Config      │  monitors.yaml — define topics, sources, schedule
└──────┬──────┘
       │
┌──────▼──────┐
│  Scheduler   │  APScheduler or simple cron loop
└──────┬──────┘
       │  triggers each monitor on its interval
┌──────▼──────┐
│  Source       │  Fetches from adapters in parallel (asyncio + httpx)
│  Fetcher      │  Normalizes to Item schema
└──────┬──────┘
       │
┌──────▼──────┐
│  Dedup       │  SQLite — hash(url) seen before? skip
└──────┬──────┘
       │
┌──────▼──────┐
│  Relevance   │  Keyword match (basic) or local embeddings (advanced)
│  Filter      │  Option A: simple keyword/regex scoring
│              │  Option B: sentence-transformers + cosine similarity (free, local)
└──────┬──────┘
       │
┌──────▼──────┐
│  Store       │  SQLite — items table with full text, metadata, scores
└──────┬──────┘
       │
┌──────▼──────┐
│  Notifier    │  Pluggable: stdout, webhook, email (SMTP), Discord/Slack/Telegram bot
└─────────────┘
```

---

## Relevance Filtering — Two Tiers

### Tier 1: Keyword + Regex (no dependencies)
- User defines keywords, phrases, exclusion patterns
- Simple TF-IDF-style scoring against item title + content
- Fast, zero overhead, good enough for most cases

### Tier 2: Local Semantic Search (optional)
- `sentence-transformers` with a small model like `all-MiniLM-L6-v2` (~80MB)
- Embed the monitor query + each item, cosine similarity threshold
- Runs locally, no API calls, gives Exa-like semantic matching
- Only load this if user opts in (keeps base agent lightweight)

---

## Config Format

```yaml
monitors:
  - name: "AI agent frameworks"
    query: "new AI agent framework OR tool for building agents"
    sources:
      - type: rss
        urls:
          - https://reddit.com/r/MachineLearning/.rss
          - https://arxiv.org/rss/cs.AI
      - type: hackernews
        search: "AI agent"
      - type: github
        query: "agent framework"
        event: release
      - type: scrape
        url: "https://example.com/blog"
        selector: "article h2 a"
    schedule: "every 30m"
    relevance: keyword  # or "semantic"
    notify:
      - type: stdout
      - type: webhook
        url: "https://hooks.slack.com/..."

  - name: "competitor pricing changes"
    sources:
      - type: change_detect
        urls:
          - https://competitor.com/pricing
        selector: ".pricing-table"
    schedule: "every 6h"
    notify:
      - type: email
        to: "me@example.com"
```

---

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Language | Python 3.12+ | Best ecosystem for scraping, feeds, ML |
| HTTP | `httpx` (async) | Modern, async-native, HTTP/2 support |
| Feed parsing | `feedparser` | Battle-tested RSS/Atom parser |
| HTML parsing | `beautifulsoup4` + `lxml` | Fast, reliable |
| JS-rendered pages | `playwright` (optional) | Only when needed — most pages don't require it |
| Storage | SQLite via `sqlite3` | Zero config, plenty fast for this scale |
| Scheduling | `APScheduler` or `asyncio` loop | Lightweight, in-process |
| Embeddings | `sentence-transformers` (optional) | Local semantic search, no API cost |
| Config | YAML (`pyyaml`) | Human-friendly |
| CLI | `click` or `typer` | Nice CLI UX |

---

## Implementation Plan

### Phase 1 — MVP (core loop)
1. Config parser (YAML → monitor definitions)
2. RSS adapter + Reddit JSON adapter + HN Algolia adapter
3. SQLite dedup + storage
4. Keyword relevance filter
5. stdout + webhook notifier
6. Simple CLI: `monitor run`, `monitor list`, `monitor results`

### Phase 2 — Richer sources
7. Web scrape adapter (CSS selector extraction)
8. Change detection adapter (diff-based)
9. GitHub adapter (releases, events)
10. Google Alerts RSS adapter

### Phase 3 — Smarter filtering
11. Local semantic search with sentence-transformers
12. Summarization (optional — local LLM or Claude API later)
13. Digest mode (batch results into periodic summary emails)

### Phase 4 — Polish
14. Dashboard (simple web UI — FastAPI + htmx or similar)
15. Persistent daemon mode with proper logging
16. Docker packaging

---

## Open Questions

- **Rate limiting**: Reddit JSON API is generous but undocumented. Need backoff/retry. HN Algolia is ~10k req/day free.
- **JS-heavy sites**: Do we include Playwright from the start or keep it optional? Leaning optional — adds ~200MB.
- **Summarization**: Without a paid LLM API, options are local models (slow on CPU) or just showing snippets. Could integrate Claude API later as an opt-in.
- **Storage scale**: SQLite is fine for thousands of items. If this grows to millions, migrate to PostgreSQL.
- **Anti-scraping**: Some sites block scrapers. Rotating user agents + respecting robots.txt + polite intervals should be baseline.
