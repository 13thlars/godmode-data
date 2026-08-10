#!/usr/bin/env python3
"""
GodMode Quiver Snapshot Collector

Purpose
-------
Pulls the Quiver endpoints used by GodMode and freezes them into one JSON file.

Why this matters
----------------
QuantConnect backtests that call live Quiver endpoints can change from run to run
because the API data changes. This script creates a frozen snapshot so GodMode V5.2+
can use a fixed input file through quiver_snapshot_url.

How to run
----------
1. Install requests:
   pip install requests

2. Set your API key:
   macOS/Linux:
     export QUIVER_API_KEY="your_key"
   Windows PowerShell:
     setx QUIVER_API_KEY "your_key"

3. Run:
   python godmode_quiver_snapshot_collector.py

Output
------
Creates:
  quiver_snapshot_YYYYMMDD.json

Then host that JSON somewhere QuantConnect can download, and set:
  quiver_snapshot_url = https://your-host/quiver_snapshot_YYYYMMDD.json
  quiver_static_mode = true
  quiver_time_mode = strict
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests


DEFAULT_ENDPOINTS = {
    "insider": "/beta/live/insiders",
    "contracts": "/beta/live/govcontractsall",
    "congress": "/beta/live/congresstrading",
    "senate": "/beta/live/senatetrading",
    "news": "/beta/live/quivernews",
    "offexchange": "/beta/live/offexchange",
    "lobbying": "/beta/live/lobbying",
    "patents": "/beta/live/allpatents",
    "13f": "/beta/live/sec13fchanges",
    "donations": "/beta/bulk/corporatedonors",
}


def build_headers(api_key: str, auth_mode: str) -> dict:
    if not api_key:
        raise ValueError("Missing QUIVER_API_KEY environment variable or --api-key")

    auth_mode = (auth_mode or "bearer").lower().strip()

    if auth_mode == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if auth_mode == "token":
        return {"Authorization": f"Token {api_key}"}
    if auth_mode == "x-api-key":
        return {"X-API-Key": api_key}

    return {"Authorization": f"Bearer {api_key}"}


def fetch_json(session: requests.Session, url: str, headers: dict, timeout: int = 60):
    response = session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    text = response.text.strip()
    if not text:
        return []

    parsed = response.json()

    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        for key in ("data", "results", "items"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return [parsed]

    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.getenv("QUIVER_API_KEY", ""))
    parser.add_argument("--auth-mode", default=os.getenv("QUIVER_AUTH_MODE", "bearer"))
    parser.add_argument("--base-url", default=os.getenv("QUIVER_BASE_URL", "https://api.quiverquant.com"))
    parser.add_argument("--out", default="")
    parser.add_argument("--max-rows", type=int, default=20000)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = build_headers(args.api_key, args.auth_mode)

    snapshot = {
        "_meta": {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "auth_mode": args.auth_mode,
            "note": "Frozen Quiver snapshot for GodMode V5.2+ research.",
        }
    }

    session = requests.Session()

    for name, path in DEFAULT_ENDPOINTS.items():
        url = base_url + path
        print(f"Fetching {name}: {url}")

        try:
            rows = fetch_json(session, url, headers)
            if args.max_rows > 0:
                rows = rows[: args.max_rows]
            snapshot[name] = rows
            print(f"  rows={len(rows)}")
        except Exception as exc:
            print(f"  ERROR {name}: {exc}")
            snapshot[name] = []

    out = args.out or f"quiver_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    Path(out).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    print("Next: host this file and set quiver_snapshot_url in QuantConnect.")


if __name__ == "__main__":
    main()
