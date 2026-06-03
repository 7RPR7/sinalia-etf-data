#!/usr/bin/env python3
"""
SinalIA — BTC ETF Flow Scraper
Scrapes Farside Investors daily BTC ETF flow table and writes
etf-flows-latest.json for the SinalIA dashboard.

Run manually:   python scraper.py
Run via CI:     GitHub Actions calls this daily at 22:00 UTC
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run: pip install requests beautifulsoup4")
    sys.exit(1)

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
OUT_FILE    = Path(__file__).parent / "etf-flows-latest.json"

# Known ETF tickers in the order Farside typically uses them
KNOWN_TICKERS = ["IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC",
                 "BRRR", "HODL", "BTCW", "BTC", "GBTC"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_flow(cell_text: str):
    """Parse a flow value like '215.6', '-52.4', '-', '' → float or None."""
    text = cell_text.strip().replace(",", "").replace("(", "-").replace(")", "")
    if not text or text in ("-", "—", "n/a", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def scrape_farside():
    resp = requests.get(FARSIDE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Farside has one main table with ETF flows
    table = soup.find("table")
    if not table:
        raise ValueError("No table found on Farside page")

    rows = table.find_all("tr")
    if len(rows) < 3:
        raise ValueError(f"Too few rows: {len(rows)}")

    # Parse header row to identify ticker columns
    header_row = rows[0]
    headers_raw = [th.get_text(strip=True).upper() for th in header_row.find_all(["th", "td"])]

    # Map column index → ticker
    col_tickers = {}
    for i, h in enumerate(headers_raw):
        for t in KNOWN_TICKERS:
            if t in h:
                col_tickers[i] = t
                break

    if not col_tickers:
        raise ValueError(f"No ticker columns found. Headers: {headers_raw}")

    # Parse data rows — skip header; collect all valid dated rows
    all_days = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        date_text = cells[0].get_text(strip=True)
        # Skip non-date rows (totals, labels, etc.)
        if not re.match(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", date_text):
            continue
        # Normalise date → YYYY-MM-DD
        try:
            if "/" in date_text:
                d = datetime.strptime(date_text, "%d/%m/%Y")
            else:
                d = datetime.strptime(date_text, "%Y-%m-%d")
            date_iso = d.strftime("%Y-%m-%d")
        except ValueError:
            continue

        flows = {}
        for col_idx, ticker in col_tickers.items():
            if col_idx < len(cells):
                v = parse_flow(cells[col_idx].get_text(strip=True))
                if v is not None:
                    flows[ticker] = v

        if flows:
            total = sum(flows.values())
            all_days.append({"date": date_iso, "flows": flows, "total": total})

    if not all_days:
        raise ValueError("No valid data rows parsed")

    # Sort by date ascending
    all_days.sort(key=lambda x: x["date"])

    # Last 14 trading days for sparkline
    last14 = all_days[-14:]

    # Most recent day
    latest = all_days[-1]
    daily_net = latest["total"]

    # Weekly net (last 5 trading days)
    weekly_days = all_days[-5:]
    weekly_net  = sum(d["total"] for d in weekly_days)

    # Cumulative (sum of all days, in $B)
    cumulative_musd = sum(d["total"] for d in all_days)
    cumulative_busd = round(cumulative_musd / 1000, 2)

    # Build byTicker using last day + weekly sum
    by_ticker = []
    for ticker in KNOWN_TICKERS:
        daily  = latest["flows"].get(ticker)
        weekly = sum(d["flows"].get(ticker, 0) for d in weekly_days)
        if daily is not None or weekly:
            by_ticker.append({
                "ticker":     ticker,
                "dailyMUSD":  round(daily, 1) if daily is not None else 0.0,
                "weeklyMUSD": round(weekly, 1),
            })

    return {
        "v":       1,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":  "farside.co.uk",
        "totals": {
            "dailyNetMUSD":   round(daily_net, 1),
            "weeklyNetMUSD":  round(weekly_net, 1),
            "cumulativeBUSD": cumulative_busd,
        },
        "byTicker": by_ticker,
        "lastDays": [
            {"date": d["date"], "totalMUSD": round(d["total"], 1)}
            for d in last14
        ],
    }


def main():
    print(f"Fetching {FARSIDE_URL} ...")
    try:
        data = scrape_farside()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    OUT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Written → {OUT_FILE}")
    print(f"  Date:       {data['lastDays'][-1]['date'] if data['lastDays'] else '?'}")
    print(f"  Daily net:  ${data['totals']['dailyNetMUSD']}M")
    print(f"  Weekly net: ${data['totals']['weeklyNetMUSD']}M")
    print(f"  Cumulative: ${data['totals']['cumulativeBUSD']}B")
    print(f"  Tickers:    {len(data['byTicker'])}")


if __name__ == "__main__":
    main()
