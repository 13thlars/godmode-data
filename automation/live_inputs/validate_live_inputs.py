#!/usr/bin/env python3
"""Validate generated GodMode live inputs before publishing them.

The validator is intentionally fail-closed: the workflow will not overwrite the
last known-good GitHub files unless both new outputs look structurally valid.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing output file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def validate_macro(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list):
        raise ValueError("Macro output must be a JSON array")
    if len(rows) < 1_000:
        raise ValueError(f"Macro output is unexpectedly small: {len(rows)} rows")

    required = {"ticker", "date", "source", "bullish_score", "risk_score", "confidence"}
    valid_rows = [row for row in rows if isinstance(row, dict) and required.issubset(row)]
    if len(valid_rows) < int(len(rows) * 0.98):
        raise ValueError("Too many malformed macro rows")

    dates = [parse_date(row.get("date")) for row in valid_rows]
    dates = [date for date in dates if date is not None]
    if not dates:
        raise ValueError("Macro output contains no parseable dates")

    latest = max(dates)
    now = datetime.now(timezone.utc)
    if latest < now - timedelta(days=10):
        raise ValueError(f"Macro output is stale; latest date is {latest.date()}")

    return {
        "rows": len(rows),
        "valid_rows": len(valid_rows),
        "unique_tickers": len({str(row.get('ticker', '')).upper() for row in valid_rows}),
        "latest_date": latest.date().isoformat(),
    }


def validate_sec(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list):
        raise ValueError("SEC output must be a JSON array")
    if len(rows) < 20:
        raise ValueError(f"SEC output is unexpectedly small: {len(rows)} rows")

    required = {"ticker", "date", "source", "bullish_score", "risk_score", "confidence"}
    valid_rows = [row for row in rows if isinstance(row, dict) and required.issubset(row)]
    if len(valid_rows) < int(len(rows) * 0.90):
        raise ValueError("Too many malformed SEC rows")

    successful = []
    for row in valid_rows:
        try:
            if float(row.get("confidence", 0)) > 0 and "fetch failed" not in str(row.get("summary", "")).lower():
                successful.append(row)
        except (TypeError, ValueError):
            continue

    successful_tickers = {str(row.get("ticker", "")).upper() for row in successful}
    if len(successful) < 20 or len(successful_tickers) < 10:
        raise ValueError(
            "SEC collection did not produce enough successful filing rows: "
            f"rows={len(successful)} tickers={len(successful_tickers)}"
        )

    dates = [parse_date(row.get("date")) for row in successful]
    dates = [date for date in dates if date is not None]

    return {
        "rows": len(rows),
        "valid_rows": len(valid_rows),
        "successful_rows": len(successful),
        "successful_tickers": len(successful_tickers),
        "latest_date": max(dates).date().isoformat() if dates else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--macro", required=True)
    parser.add_argument("--sec", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args()

    macro_path = Path(args.macro)
    sec_path = Path(args.sec)
    status_path = Path(args.status)

    macro = validate_macro(load_json(macro_path))
    sec = validate_sec(load_json(sec_path))

    status = {
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "github_actions",
        "github_run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "github_sha": os.getenv("GITHUB_SHA", "local"),
        "macro": macro,
        "sec": sec,
    }

    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    print("Validation passed")
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
