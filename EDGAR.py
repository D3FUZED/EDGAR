# EDGAR.py

import os
import json
import logging
import requests
import feedparser
import re
from datetime import datetime

# ------------------------------------------------------------
# 1. CONFIGURATION
# ------------------------------------------------------------
CIKS = {
    "Kraken": "0001763926",
    "Gemini": "0001845748",
    "Ripple": "0001551332",
    "BitGo":  "0001835212",
}
EDGAR_RSS_URL = "https://www.sec.gov/Archives/edgar/usgaap.rss"
RSS_KEYWORDS  = ["crypto", "blockchain"]

WEBHOOK        = os.environ["DISCORD_WEBHOOK"]
USER_AGENT     = os.environ.get("USER_AGENT", "SEC WATCHER")
STATE_FILE     = os.environ.get("STATE_FILE", "seen_entries.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s)")

# ------------------------------------------------------------
# 2. STATE MANAGEMENT
# ------------------------------------------------------------
try:
    with open(STATE_FILE) as f:
        seen = set(tuple(item) for item in json.load(f))
except FileNotFoundError:
    seen = set()

def save_state():
    with open(STATE_FILE, 'w') as f:
        json.dump(list(seen), f)
    logging.debug("State saved (%d entries)", len(seen))


# ------------------------------------------------------------
# 3. UTILITIES
# ------------------------------------------------------------
def fetch_filings(cik):
    url     = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    headers = {"User-Agent": USER_AGENT}
    r       = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    recent  = r.json().get("filings", {}).get("recent", {})
    return list(zip(
        recent.get("accessionNumber", []),
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("primaryDocument", [])
    ))

def fetch_rss_entries():
    return feedparser.parse(EDGAR_RSS_URL).entries

def extract_offering_details(text):
    details = {}
    m_amt = re.search(
        r"maximum\s+aggregate\s+offering\s+price\s*[:\-]?\s*\$?([0-9,]+\.?[0-9]*)",
        text, re.IGNORECASE
    )
    if m_amt:
        details["Amount"] = m_amt.group(1)
    m_tkr = re.search(
        r"proposed\s+ticker\s+symbol\s*[:\-]?\s*([A-Z]{1,5})",
        text, re.IGNORECASE
    )
    if m_tkr:
        details["Ticker"] = m_tkr.group(1)
    return details

def post_embed(title, desc, url, fields=None):
    embed = {
        "title":       title,
        "description": desc,
        "url":         url,
        "timestamp":   datetime.utcnow().isoformat(),
        "fields":      []
    }
    if fields:
        for n, v in fields.items():
            embed["fields"].append({"name": n, "value": v, "inline": True})
    resp = requests.post(WEBHOOK, json={"embeds":[embed]})
    resp.raise_for_status()

# ------------------------------------------------------------
# 4. HANDLER FUNCTIONS
# ------------------------------------------------------------
def handle_company(name, acc, form, date, doc):
    path      = acc.replace("-", "")
    filing_url= f"https://www.sec.gov/Archives/edgar/data/{CIKS[name]}/{path}/{doc}"
    # fetch snippet
    snippet   = requests.get(filing_url.replace(".htm",".txt"),
                             headers={"User-Agent":USER_AGENT},
                             timeout=10).text[:2000]
    details   = extract_offering_details(snippet)
    title     = f"{name} filed {form}"
    desc      = f"Date: {date}"
    post_embed(title, desc, filing_url, details)
    logging.info("Notified: %s %s", name, form)

def handle_industry(entry):
    title   = entry.title
    link    = entry.link
    summary = entry.summary.replace("\n"," ")
    snippet = (summary[:200]+"...") if len(summary)>200 else summary
    post_embed(f"Industry Filing: {title}",
               snippet,
               link)
    logging.info("Industry: %s", title)

# ------------------------------------------------------------
# 5. MAIN (single run)
# ------------------------------------------------------------
def main():
    # Company-specific filings
    for name, cik in CIKS.items():
        logging.info("Checking filings for %s (CIK %s)…", name, cik)
        for acc, form, date, doc in fetch_filings(cik):
            key = ("CIK", name, acc)
            if key not in seen and form in ("S-1","F-1","D-1"):
                handle_company(name, acc, form, date, doc)
            seen.add(key)

    # Industry RSS filings
    logging.info("Checking industry RSS entries…")
    for entry in fetch_rss_entries():
        key = ("RSS", entry.id)
        text= (entry.title+entry.summary).lower()
        if key not in seen and any(k in text for k in RSS_KEYWORDS):
            handle_industry(entry)
        seen.add(key)

    save_state()

if __name__ == "__main__":
    main()
