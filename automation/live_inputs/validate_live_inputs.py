#!/usr/bin/env python3
"""Fail-closed validation for deterministic GodMode live inputs."""
from __future__ import annotations
import argparse, json, os
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
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def validate_macro(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list) or len(rows) < 1000:
        raise ValueError(f"Macro output is invalid or too small: {len(rows) if isinstance(rows,list) else 'not-list'}")
    required={"ticker","date","source","bullish_score","risk_score","confidence"}
    valid=[r for r in rows if isinstance(r,dict) and required.issubset(r)]
    if len(valid) < int(len(rows)*0.98):
        raise ValueError("Too many malformed macro rows")
    dates=[d for d in (parse_date(r.get("date")) for r in valid) if d]
    if not dates:
        raise ValueError("Macro output contains no parseable dates")
    latest=max(dates)
    if latest < datetime.now(timezone.utc)-timedelta(days=10):
        raise ValueError(f"Macro output is stale: {latest.date()}")
    return {"rows":len(rows),"valid_rows":len(valid),"unique_tickers":len({str(r.get('ticker','')).upper() for r in valid}),"latest_date":latest.date().isoformat()}


def validate_sec(rows: Any) -> dict[str, Any]:
    if not isinstance(rows,list) or len(rows)<20:
        raise ValueError(f"SEC output is invalid or too small: {len(rows) if isinstance(rows,list) else 'not-list'}")
    required={"ticker","date","source","bullish_score","risk_score","confidence"}
    valid=[r for r in rows if isinstance(r,dict) and required.issubset(r)]
    successful=[]
    for r in valid:
        try:
            if float(r.get("confidence",0))>0 and "fetch failed" not in str(r.get("summary","")).lower():
                successful.append(r)
        except (TypeError,ValueError):
            pass
    tickers={str(r.get("ticker","")).upper() for r in successful}
    if len(successful)<20 or len(tickers)<10:
        raise ValueError(f"SEC collection too small: rows={len(successful)} tickers={len(tickers)}")
    dates=[d for d in (parse_date(r.get("date")) for r in successful) if d]
    return {"rows":len(rows),"valid_rows":len(valid),"successful_rows":len(successful),"successful_tickers":len(tickers),"latest_date":max(dates).date().isoformat() if dates else None}


def validate_quiver(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot,dict):
        raise ValueError("Quiver snapshot must be a JSON object")
    endpoints=[k for k,v in snapshot.items() if not str(k).startswith("_") and isinstance(v,list)]
    counts={k:len(snapshot[k]) for k in endpoints}
    nonempty=[k for k,c in counts.items() if c>0]
    total=sum(counts.values())
    if len(endpoints)<8:
        raise ValueError(f"Quiver snapshot has too few endpoints: {len(endpoints)}")
    if len(nonempty)<4 or total<300:
        raise ValueError(f"Quiver snapshot is unexpectedly sparse: nonempty={len(nonempty)} total_rows={total}")
    meta=snapshot.get("_meta",{}) if isinstance(snapshot.get("_meta",{}),dict) else {}
    created=meta.get("created_utc")
    return {"endpoints":len(endpoints),"nonempty_endpoints":len(nonempty),"total_rows":total,"rows_by_endpoint":counts,"created_utc":created}


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--macro",required=True)
    p.add_argument("--sec",required=True)
    p.add_argument("--quiver",required=True)
    p.add_argument("--status",required=True)
    a=p.parse_args()
    status={
        "status":"ok",
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "source":"github_actions_deterministic_ab",
        "github_run_id":os.getenv("GITHUB_RUN_ID","local"),
        "github_sha":os.getenv("GITHUB_SHA","local"),
        "macro":validate_macro(load_json(Path(a.macro))),
        "sec":validate_sec(load_json(Path(a.sec))),
        "quiver":validate_quiver(load_json(Path(a.quiver))),
    }
    path=Path(a.status); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(status,indent=2)+"\n",encoding="utf-8")
    print("Validation passed")
    print(json.dumps(status,indent=2))

if __name__=="__main__":
    main()
