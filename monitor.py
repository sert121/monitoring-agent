#!/usr/bin/env python3
"""
HN Monitoring Agent

Polls the Hacker News Algolia API for new stories matching your queries.
Outputs JSONL to stdout (one JSON object per line). Optionally POSTs
each result to a webhook URL.

On startup, seeds the dedup database with existing results so you only
see items that appear *after* the job begins.

Usage:
    python monitor.py run "AI agents"                        # one-shot seed
    python monitor.py run "AI agents" --watch                # poll every 5m
    python monitor.py run "llm:LLM inference" "rag:RAG"     # labeled monitors
    python monitor.py run "AI" --webhook https://hook.url    # POST results too
    python monitor.py search "Show HN"                       # quick search, no dedup
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "monitor.db"
HN_API = "https://hn.algolia.com/api/v1/search_by_date"
USER_AGENT = "monitoring-agent/0.1"

# ---------------------------------------------------------------------------
# Dedup database — tracks which HN items we've already seen
# ---------------------------------------------------------------------------

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts TEXT)")
    db.commit()
    return db


def already_seen(db, item_id):
    return db.execute("SELECT 1 FROM seen WHERE id = ?", (item_id,)).fetchone() is not None


def mark_seen(db, item_id):
    db.execute("INSERT OR IGNORE INTO seen (id, ts) VALUES (?, ?)",
               (item_id, now_iso()))
    db.commit()

# ---------------------------------------------------------------------------
# HN Algolia API
# ---------------------------------------------------------------------------

def fetch_hn(query, tags="story", hours_back=24, limit=20):
    """Search HN for stories matching `query` posted in the last `hours_back` hours."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp())
    url = f"{HN_API}?{urllib.parse.urlencode({
        'query':          query,
        'tags':           tags,
        'numericFilters': f'created_at_i>{cutoff}',
        'hitsPerPage':    str(limit),
    })}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("hits", [])

# ---------------------------------------------------------------------------
# Output helpers — everything goes to stdout as JSONL
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_hit(hit, monitor=None):
    """Turn a raw Algolia hit into a clean record."""
    hn_id = hit["objectID"]
    return {
        "id":           hn_id,
        "title":        hit.get("title") or "",
        "url":          hit.get("url") or f"https://news.ycombinator.com/item?id={hn_id}",
        "hn_url":       f"https://news.ycombinator.com/item?id={hn_id}",
        "points":       hit.get("points", 0),
        "comments":     hit.get("num_comments", 0),
        "author":       hit.get("author", ""),
        "created_at":   hit.get("created_at", ""),
        "monitor":      monitor,
    }


def emit(record, webhook=None):
    """Write one JSONL line to stdout, optionally POST to webhook."""
    print(json.dumps(record), flush=True)
    if webhook:
        _post_webhook(webhook, record)


def emit_event(kind, data, webhook=None):
    """Emit a lifecycle event (_event field distinguishes these from results)."""
    emit({"_event": kind, **data, "_ts": now_iso()}, webhook)


def _post_webhook(url, payload):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(json.dumps({"_error": f"webhook: {e}"}), file=sys.stderr, flush=True)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def parse_queries(raw):
    """Parse 'name:query' pairs. If no name given, use the query itself."""
    queries = []
    for q in raw:
        if ":" in q:
            name, query = q.split(":", 1)
            queries.append({"name": name, "query": query})
        else:
            queries.append({"name": q, "query": q})
    return queries


def check_monitor(db, name, query, args):
    """Fetch HN results for one monitor, emit only unseen items."""
    emit_event("checking", {"monitor": name, "query": query}, args.webhook)

    try:
        hits = fetch_hn(query, tags=args.tags, hours_back=args.hours_back, limit=args.hits)
    except Exception as e:
        emit_event("error", {"monitor": name, "error": str(e)}, args.webhook)
        return

    new = 0
    for hit in hits:
        if already_seen(db, hit["objectID"]):
            continue
        mark_seen(db, hit["objectID"])
        emit(normalize_hit(hit, monitor=name), args.webhook)
        new += 1

    emit_event("check_done", {"monitor": name, "new": new, "total": len(hits)}, args.webhook)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args):
    """Run monitors: seed existing results, then poll for new ones."""
    db = init_db()
    monitors = parse_queries(args.query)

    emit_event("start", {
        "monitors":   [m["name"] for m in monitors],
        "interval":   args.interval,
        "hours_back": args.hours_back,
    }, args.webhook)

    # First check — show all current results for the query
    for m in monitors:
        check_monitor(db, m["name"], m["query"], args)

    if not args.watch:
        db.close()
        return

    # Subsequent checks — only new items (dedup handles the rest)
    try:
        while True:
            emit_event("sleeping", {"seconds": args.interval}, args.webhook)
            time.sleep(args.interval)
            for m in monitors:
                check_monitor(db, m["name"], m["query"], args)
    except KeyboardInterrupt:
        emit_event("stopped", {}, args.webhook)
    finally:
        db.close()


def cmd_search(args):
    """One-off search — no dedup, no persistence. Just fetch and print."""
    hits = fetch_hn(args.query, tags=args.tags, hours_back=args.hours_back, limit=args.hits)
    for hit in hits:
        emit(normalize_hit(hit), args.webhook)
    emit_event("search_done", {"query": args.query, "results": len(hits)}, args.webhook)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="HN monitoring agent — outputs JSONL to stdout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Start monitoring (seed + optional watch loop)")
    p_run.add_argument("query", nargs="+", help="Queries to monitor. Use 'name:query' to label them.")
    p_run.add_argument("--tags", default="story", help="HN content type (default: story)")
    p_run.add_argument("--hours-back", type=int, default=24, help="Lookback window in hours (default: 24)")
    p_run.add_argument("--hits", type=int, default=20, help="Max results per query per check (default: 20)")
    p_run.add_argument("--watch", action="store_true", help="Keep polling after initial seed")
    p_run.add_argument("--interval", type=int, default=300, help="Seconds between polls (default: 300)")
    p_run.add_argument("--webhook", default=None, help="POST each result to this URL")

    p_search = sub.add_parser("search", help="One-off search (no dedup)")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--tags", default="story")
    p_search.add_argument("--hours-back", type=int, default=24)
    p_search.add_argument("--hits", type=int, default=10)
    p_search.add_argument("--webhook", default=None)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"run": cmd_run, "search": cmd_search}[args.command](args)


if __name__ == "__main__":
    main()
