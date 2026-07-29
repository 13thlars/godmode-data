#!/usr/bin/env python3
"""
GodMode V5.4 Chronos Macro History Factory

This produces a date-aware macro signal file:
  macro_history_signals_YYYYMMDD.json

Unlike the one-day macro file, this creates historical weekly rows from FRED
so QuantConnect can use the latest macro signal available as of each backtest date.

Run:
  pip install requests pandas
  setx FRED_API_KEY "your_key"  (Windows, then reopen PowerShell)
  python godmode_macro_history_factory.py --start 2023-01-01 --out macro_history_signals.json

Then upload the JSON to GitHub raw and set:
  macro_signal_url = https://raw.githubusercontent.com/.../macro_history_signals.json
  external_date_mode = latest_before
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


FRED_SERIES = {
    "dgs10": "DGS10",
    "dgs2": "DGS2",
    "real10y": "DFII10",
    "hy_oas": "BAMLH0A0HYM2",
    "ig_oas": "BAMLC0A0CM",
    "vix": "VIXCLS",
    "dollar": "DTWEXBGS",
    "oil": "DCOILWTICO",
    "claims": "ICSA",
    "fed_assets": "WALCL",
    "rrp": "RRPONTSYD",
}

GROWTH_AI_TICKERS = [
    "NVDA", "AVGO", "MSFT", "META", "AMZN", "GOOG", "ANET", "MU", "LRCX", "AMAT",
    "KLAC", "QCOM", "TSM", "MPWR", "MRVL", "VRT", "ETN", "PWR", "HUBB",
    "GEV", "CAT", "TT", "APH", "NXPI", "ADI", "TXN", "IBM", "ORCL", "CRM", "FLEX"
]


def fred_series(api_key, series_id, start):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    rows = r.json().get("observations", [])
    dates, vals = [], []
    for row in rows:
        v = row.get("value")
        if v in (None, ".", ""):
            continue
        try:
            dates.append(pd.to_datetime(row["date"]))
            vals.append(float(v))
        except Exception:
            pass
    return pd.Series(vals, index=dates).sort_index()


def z_change(s, current_date, lookback_days=30):
    s = s.dropna()
    s = s[s.index <= current_date]
    if len(s) < 25:
        return 0.0
    current = s.iloc[-1]
    prior_date = current_date - pd.Timedelta(days=lookback_days)
    prior_s = s[s.index <= prior_date]
    if len(prior_s) == 0:
        return 0.0
    prior = prior_s.iloc[-1]
    diff_series = s.diff().dropna().tail(60)
    vol = diff_series.std()
    if not pd.notna(vol) or vol == 0:
        return 0.0
    return float((current - prior) / vol)


def pct_change(s, current_date, lookback_days=30):
    s = s.dropna()
    s = s[s.index <= current_date]
    if len(s) < 25:
        return 0.0
    current = s.iloc[-1]
    prior_date = current_date - pd.Timedelta(days=lookback_days)
    prior_s = s[s.index <= prior_date]
    if len(prior_s) == 0:
        return 0.0
    prior = prior_s.iloc[-1]
    if prior == 0:
        return 0.0
    return float(current / prior - 1.0)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def score_date(data, current_date):
    risk = 0.0
    bull = 5.0
    reasons = []

    real_yield_trend = z_change(data["real10y"], current_date)
    hy_trend = z_change(data["hy_oas"], current_date)
    ig_trend = z_change(data["ig_oas"], current_date)
    vix_trend = z_change(data["vix"], current_date)
    dollar_trend = z_change(data["dollar"], current_date)
    oil_trend = pct_change(data["oil"], current_date)
    claims_trend = z_change(data["claims"], current_date)

    liquidity_trend = 0.0
    if not data["fed_assets"].empty and not data["rrp"].empty:
        df = pd.concat([data["fed_assets"], data["rrp"]], axis=1).dropna()
        df = df[df.index <= current_date]
        if len(df) > 30:
            net_liq = df.iloc[:, 0] - df.iloc[:, 1]
            liquidity_trend = z_change(net_liq, current_date)

    if real_yield_trend < -0.75:
        bull += 1.3
        reasons.append("falling real yields")
    elif real_yield_trend > 0.75:
        risk += 1.3
        bull -= 0.8
        reasons.append("rising real yields")

    if hy_trend > 0.75 or ig_trend > 0.75:
        risk += 1.8
        bull -= 1.2
        reasons.append("credit spreads widening")
    elif hy_trend < -0.75 and ig_trend < -0.25:
        bull += 1.2
        reasons.append("credit easing")

    if vix_trend > 0.75:
        risk += 0.8
        reasons.append("volatility rising")
    elif vix_trend < -0.75:
        bull += 0.5
        reasons.append("volatility easing")

    if dollar_trend > 0.75:
        risk += 0.6
        reasons.append("dollar strengthening")
    elif dollar_trend < -0.75:
        bull += 0.4
        reasons.append("dollar easing")

    if oil_trend > 0.10:
        risk += 0.6
        reasons.append("oil pressure")

    if claims_trend > 0.75:
        risk += 0.9
        bull -= 0.6
        reasons.append("claims deteriorating")
    elif claims_trend < -0.75:
        bull += 0.5
        reasons.append("claims improving")

    if liquidity_trend > 0.75:
        bull += 0.8
        reasons.append("liquidity improving")
    elif liquidity_trend < -0.75:
        risk += 0.8
        reasons.append("liquidity tightening")

    bull = clamp(bull, 0, 10)
    risk = clamp(risk, 0, 10)
    confidence = 7 if reasons else 5
    return round(bull, 2), round(risk, 2), confidence, "; ".join(reasons) if reasons else "macro neutral"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-key", default=os.getenv("FRED_API_KEY", ""))
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    p.add_argument("--freq", default="W-FRI")
    p.add_argument("--out", default="")
    args = p.parse_args()

    if not args.api_key:
        raise ValueError("Missing FRED_API_KEY environment variable or --api-key")

    data = {}
    for name, sid in FRED_SERIES.items():
        print(f"Fetching {name}: {sid}")
        data[name] = fred_series(args.api_key, sid, args.start)
        print(f"  points={len(data[name])}")

    dates = pd.date_range(args.start, args.end, freq=args.freq)
    signals = []

    for d in dates:
        bull, risk, conf, summary = score_date(data, d)
        date_text = d.strftime("%Y-%m-%d")
        for ticker in GROWTH_AI_TICKERS:
            signals.append({
                "ticker": ticker,
                "date": date_text,
                "source": "macro_chronos",
                "bullish_score": bull,
                "risk_score": risk,
                "confidence": conf,
                "summary": summary,
            })

    out = args.out or f"macro_history_signals_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    Path(out).write_text(json.dumps(signals, indent=2), encoding="utf-8")
    print(f"Wrote {out} rows={len(signals)}")


if __name__ == "__main__":
    main()
