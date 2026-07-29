#!/usr/bin/env python3
"""
GodMode SEC Scribe Factory V2 - Expanded Universe

Why this version exists
-----------------------
The first SEC Scribe file only covered a small AI/growth list. The V5.5 observe
run showed Scribe was loading, but it only saw a tiny number of risky tickers.
This version scans a wider universe built from actual GodMode traded names.

How to use
----------
1. Put this file and godmode_scribe_universe.txt in C:\GodMode
2. Install:
   pip install requests beautifulsoup4
3. Run:
   python godmode_sec_scribe_factory_v2.py --out sec_scribe_signals_wide.json --user-agent "Your Name your.email@example.com"
4. Upload sec_scribe_signals_wide.json to GitHub.
5. Use the Raw URL in QuantConnect:
   sec_signal_url = https://raw.githubusercontent.com/13thlars/godmode-data/main/sec_scribe_signals_wide.json

Recommended QuantConnect tests
------------------------------
First:
  scribe_mode = observe

Then:
  scribe_mode = risk_only

Do not use scribe_mode=active until risk_only proves value.
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


DEFAULT_TICKERS = [
    "NVDA","AVGO","MSFT","META","AMZN","GOOG","ANET","MU","LRCX","AMAT","KLAC","QCOM",
    "TSM","MPWR","MRVL","VRT","ETN","PWR","HUBB","GEV","CAT","TT","APH","NXPI","ADI",
    "TXN","IBM","ORCL","CRM","FLEX","DELL","AMKR","WFC","PYPL","MCK","AAPL","GM","BMY",
    "SLB","ABT","UBER","MA","NEE","GS","TMUS","C","T","ENTG","GD","AXP"
]


NEGATIVE_TERMS = {
    "going concern": 10,
    "substantial doubt": 10,
    "material weakness": 8,
    "restatement": 8,
    "default": 8,
    "liquidity constraints": 7,
    "sec investigation": 7,
    "subpoena": 6,
    "dilution": 5,
    "at-the-market offering": 5,
    "common stock offering": 5,
    "convertible notes": 4,
    "impairment": 5,
    "pricing pressure": 4,
    "margin pressure": 4,
    "destocking": 4,
    "inventory correction": 4,
    "customer delays": 4,
    "elongated sales cycles": 4,
    "demand softness": 4,
    "restructuring": 3,
    "layoff": 3,
    "regulatory investigation": 5,
    "litigation": 3,
    "cybersecurity incident": 4,
}

POSITIVE_TERMS = {
    "artificial intelligence": 2,
    "ai demand": 5,
    "data center": 4,
    "accelerated computing": 4,
    "strong demand": 4,
    "record revenue": 4,
    "margin expansion": 3,
    "pricing power": 4,
    "backlog": 3,
    "contract award": 4,
    "strategic partnership": 3,
    "share repurchase": 3,
    "increased guidance": 4,
    "raise guidance": 4,
    "free cash flow": 2,
}


def load_tickers(path):
    p = Path(path)
    if not p.exists():
        return DEFAULT_TICKERS
    out = []
    for line in p.read_text().splitlines():
        t = line.strip().upper()
        if t and re.match(r"^[A-Z.]{1,6}$", t) and t not in out:
            out.append(t)
    return out or DEFAULT_TICKERS


def get_company_tickers(headers):
    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    out = {}
    for item in data.values():
        ticker = item["ticker"].upper()
        cik = str(item["cik_str"]).zfill(10)
        out[ticker] = cik
    return out


def get_submissions(cik, headers):
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def filing_text_url(cik, accession, primary_doc):
    accession_clean = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{primary_doc}"


def fetch_text(url, headers):
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return soup.get_text(" ", strip=True).lower()


def score_text(text):
    positive = 0
    risk = 0
    hits_pos = []
    hits_neg = []

    for term, points in POSITIVE_TERMS.items():
        count = len(re.findall(re.escape(term), text))
        if count:
            positive += min(points * count, points * 4)
            hits_pos.append(term)

    for term, points in NEGATIVE_TERMS.items():
        count = len(re.findall(re.escape(term), text))
        if count:
            risk += min(points * count, points * 4)
            hits_neg.append(term)

    bullish = max(-10, min(10, 5 + positive * 0.45 - risk * 0.70))
    risk_score = max(0, min(10, risk * 0.45))
    confidence = 8 if hits_neg else (7 if hits_pos else 4)
    summary = "pos=" + ",".join(hits_pos[:5]) + " neg=" + ",".join(hits_neg[:5])
    return bullish, risk_score, confidence, summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="sec_scribe_signals_wide.json")
    p.add_argument("--tickers-file", default="godmode_scribe_universe.txt")
    p.add_argument("--user-agent", default="GodModeResearch contact@example.com")
    p.add_argument("--max-filings", type=int, default=4)
    p.add_argument("--sleep", type=float, default=0.12)
    args = p.parse_args()

    headers = {"User-Agent": args.user_agent}
    tickers = load_tickers(args.tickers_file)
    ticker_to_cik = get_company_tickers(headers)

    print(f"Scanning {len(tickers)} tickers")
    signals = []

    for ticker in tickers:
        cik = ticker_to_cik.get(ticker)
        if not cik:
            print(f"Missing CIK for {ticker}")
            continue

        try:
            submissions = get_submissions(cik, headers)
            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            docs = recent.get("primaryDocument", [])

            checked = 0
            for form, filing_date, accession, doc in zip(forms, dates, accessions, docs):
                if form not in ["10-K", "10-Q", "8-K", "S-3", "424B5", "10-K/A", "10-Q/A"]:
                    continue
                if checked >= args.max_filings:
                    break

                url = filing_text_url(cik, accession, doc)
                text = fetch_text(url, headers)
                bullish, risk, conf, summary = score_text(text)

                signals.append({
                    "ticker": ticker,
                    "date": filing_date,
                    "source": "sec_scribe_v2",
                    "form": form,
                    "bullish_score": round(bullish, 2),
                    "risk_score": round(risk, 2),
                    "confidence": conf,
                    "summary": f"{form}: {summary}",
                })

                checked += 1
                time.sleep(args.sleep)

            print(f"{ticker}: {checked} filings")

        except Exception as e:
            print(f"{ticker}: ERROR {e}")
            signals.append({
                "ticker": ticker,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "source": "sec_scribe_v2",
                "bullish_score": 0,
                "risk_score": 0,
                "confidence": 0,
                "summary": f"SEC fetch failed: {str(e)[:120]}",
            })

    Path(args.out).write_text(json.dumps(signals, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} rows={len(signals)}")


if __name__ == "__main__":
    main()
