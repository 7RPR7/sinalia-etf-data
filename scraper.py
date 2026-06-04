#!/usr/bin/env python3
"""
SinalIA — BTC ETF Flow Scraper  v2
Sources (in order of preference):
  1. Farside Investors  — full history, session-based scrape
  2. CoinGlass open API — no auth, daily net flows
  3. SoSoValue API      — no auth, daily ETF data
  4. Static seed        — always succeeds; keeps the panel alive
"""
 
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
 
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing: pip install requests beautifulsoup4")
    sys.exit(1)
 
OUT_FILE = Path(__file__).parent / "etf-flows-latest.json"
 
KNOWN_TICKERS = ["IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC",
                 "BRRR", "HODL", "BTCW", "BTC", "GBTC"]
 
# Full browser fingerprint — session-based to get cookies first
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
 
# ── helpers ───────────────────────────────────────────────────────────────────
 
def parse_flow(text):
    t = text.strip().replace(",", "").replace("(", "-").replace(")", "")
    if not t or t in ("-", "—", "n/a", "N/A", ""):
        return None
    try:
        return float(t)
    except ValueError:
        return None
 
def build_output(all_days, by_ticker_map, source):
    all_days.sort(key=lambda x: x["date"])
    last5  = all_days[-5:]
    last14 = all_days[-14:]
    latest = all_days[-1] if all_days else None
    daily  = latest["total"] if latest else 0
    weekly = sum(d["total"] for d in last5)
    cum_musd = sum(d["total"] for d in all_days)
 
    by_ticker = []
    for t in KNOWN_TICKERS:
        d5 = by_ticker_map.get(t, {})
        daily_v  = d5.get("daily")
        weekly_v = d5.get("weekly", 0)
        if daily_v is not None or weekly_v:
            by_ticker.append({
                "ticker":     t,
                "dailyMUSD":  round(daily_v, 1) if daily_v is not None else 0.0,
                "weeklyMUSD": round(weekly_v, 1),
            })
 
    return {
        "v":       1,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":  source,
        "totals": {
            "dailyNetMUSD":   round(daily, 1),
            "weeklyNetMUSD":  round(weekly, 1),
            "cumulativeBUSD": round(cum_musd / 1000, 2),
        },
        "byTicker": by_ticker,
        "lastDays": [
            {"date": d["date"], "totalMUSD": round(d["total"], 1)}
            for d in last14
        ],
    }
 
# ── Source 1: Farside (session-based) ────────────────────────────────────────
 
def scrape_farside():
    FARSIDE = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
    session = requests.Session()
 
    # Step 1: Visit homepage to get cookies + warm up session
    session.get("https://farside.co.uk/", headers=BROWSER_HEADERS, timeout=15)
    time.sleep(1.5)
 
    # Step 2: Fetch data page
    resp = session.get(FARSIDE, headers={
        **BROWSER_HEADERS,
        "Referer": "https://farside.co.uk/",
        "Sec-Fetch-Site": "same-origin",
    }, timeout=20)
    resp.raise_for_status()
 
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("No table found")
 
    rows = table.find_all("tr")
    if len(rows) < 3:
        raise ValueError(f"Too few rows: {len(rows)}")
 
    headers_raw = [th.get_text(strip=True).upper()
                   for th in rows[0].find_all(["th", "td"])]
 
    col_tickers = {}
    for i, h in enumerate(headers_raw):
        for t in KNOWN_TICKERS:
            if t in h:
                col_tickers[i] = t
                break
 
    if not col_tickers:
        raise ValueError(f"No ticker columns. Headers: {headers_raw}")
 
    all_days = []
    last5_flows = {t: [] for t in KNOWN_TICKERS}
 
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        date_text = cells[0].get_text(strip=True)
        if not re.match(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", date_text):
            continue
        try:
            d = (datetime.strptime(date_text, "%d/%m/%Y")
                 if "/" in date_text
                 else datetime.strptime(date_text, "%Y-%m-%d"))
            date_iso = d.strftime("%Y-%m-%d")
        except ValueError:
            continue
 
        flows = {}
        for ci, ticker in col_tickers.items():
            if ci < len(cells):
                v = parse_flow(cells[ci].get_text(strip=True))
                if v is not None:
                    flows[ticker] = v
 
        if flows:
            total = sum(flows.values())
            all_days.append({"date": date_iso, "flows": flows, "total": total})
 
    if not all_days:
        raise ValueError("No valid rows")
 
    all_days.sort(key=lambda x: x["date"])
    last5 = all_days[-5:]
    latest = all_days[-1]
 
    by_ticker_map = {}
    for t in KNOWN_TICKERS:
        by_ticker_map[t] = {
            "daily":  latest["flows"].get(t),
            "weekly": sum(d["flows"].get(t, 0) for d in last5),
        }
 
    return build_output(all_days, by_ticker_map, "farside.co.uk")
 
# ── Source 2: CoinGlass public API ────────────────────────────────────────────
 
def scrape_coinglass():
    """CoinGlass has a public (no-auth) endpoint for BTC ETF net flows."""
    url = "https://open-api.coinglass.com/public/v2/indicator/etf?symbol=BTC"
    resp = requests.get(url, headers={
        "User-Agent": BROWSER_HEADERS["User-Agent"],
        "Accept": "application/json",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
 
    if data.get("code") != "0" and not data.get("data"):
        raise ValueError(f"CoinGlass error: {data.get('msg','')}")
 
    items = data.get("data", [])
    if not items:
        raise ValueError("CoinGlass: empty data")
 
    all_days = []
    by_ticker_map = {}
 
    for item in items:
        date_ts = item.get("date") or item.get("time")
        if date_ts:
            d = datetime.fromtimestamp(int(date_ts) / 1000, tz=timezone.utc)
            date_iso = d.strftime("%Y-%m-%d")
        else:
            continue
 
        net = item.get("netInflow") or item.get("total") or 0
        all_days.append({"date": date_iso, "total": float(net), "flows": {}})
 
    if not all_days:
        raise ValueError("CoinGlass: no daily data")
 
    # Best effort per-ticker from the last item
    last_item = items[-1] if items else {}
    for t in KNOWN_TICKERS:
        v = last_item.get(t.lower()) or last_item.get(t)
        if v is not None:
            by_ticker_map[t] = {"daily": float(v), "weekly": float(v) * 5}
 
    return build_output(all_days, by_ticker_map, "coinglass.com")
 
# ── Source 3: SoSoValue API ──────────────────────────────────────────────────
 
def scrape_sosovalue():
    """SoSoValue publishes BTC ETF daily data via a JSON API."""
    url = "https://sosovalue.com/api/etf/us-btc-spot/daily-flows"
    resp = requests.get(url, headers={
        "User-Agent": BROWSER_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Referer": "https://sosovalue.com/",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
 
    rows = data.get("data") or data.get("rows") or data
    if not isinstance(rows, list) or not rows:
        raise ValueError("SoSoValue: unexpected response")
 
    all_days = []
    by_ticker_map = {}
 
    for row in rows:
        date_str = row.get("date") or row.get("day") or ""
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            date_iso = dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
        net = row.get("totalNetInflow") or row.get("netInflow") or row.get("total") or 0
        all_days.append({"date": date_iso, "total": float(net) / 1e6, "flows": {}})
 
    if not all_days:
        raise ValueError("SoSoValue: no rows parsed")
 
    return build_output(all_days, by_ticker_map, "sosovalue.com")
 
# ── Source 4: Static seed (never fails) ──────────────────────────────────────
 
def static_seed():
    print("  ⚠ All live sources failed — writing refreshed seed data.")
    return {
        "v": 1,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "bootstrap-seed",
        "totals": {"dailyNetMUSD": 0.0, "weeklyNetMUSD": 0.0, "cumulativeBUSD": 31.4},
        "byTicker": [{"ticker": t, "dailyMUSD": 0.0, "weeklyMUSD": 0.0}
                     for t in KNOWN_TICKERS],
        "lastDays": [],
    }
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
SOURCES = [
    ("Farside",    scrape_farside),
    ("CoinGlass",  scrape_coinglass),
    ("SoSoValue",  scrape_sosovalue),
]
 
def main():
    print(f"Fetching BTC ETF flow data…")
    data = None
 
    for name, fn in SOURCES:
        print(f"  → Trying {name}…", end=" ", flush=True)
        try:
            data = fn()
            print(f"✓  ({data['totals']['dailyNetMUSD']:+.1f} M daily)")
            break
        except Exception as e:
            print(f"✗  {e}")
 
    if data is None:
        data = static_seed()
 
    OUT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nWritten → {OUT_FILE.name}")
    print(f"  Source:     {data['source']}")
    print(f"  Daily net:  ${data['totals']['dailyNetMUSD']}M")
    print(f"  Weekly net: ${data['totals']['weeklyNetMUSD']}M")
    print(f"  Cumulative: ${data['totals']['cumulativeBUSD']}B")
 
    # Only exit with error if we wrote a seed (no live data)
    if data["source"] == "bootstrap-seed":
        sys.exit(1)
 
 
if __name__ == "__main__":
    main()
