# edgar_ipo_notifier.py
import os
import time
import json
import logging
import requests
import feedparser
import re
from datetime import datetime

# ------------------------------------------------------------
# 1. CONFIGURATION
# ------------------------------------------------------------
# Monitored companies with their SEC CIKs
CIKS = {
    "Kraken": "0001763926",
    "Gemini": "0001845748",
    "Ripple": "0001551332",
    "BitGo":  "0001835212",
}
# RSS feed for general industry filings
EDGAR_RSS_URL = "https://www.sec.gov/Archives/edgar/usgaap.rss"
# Keywords for general crypto/blockchain industry filtering
RSS_KEYWORDS = ["crypto", "blockchain"]

# Discord webhook URL must be set as an environment variable
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
if not WEBHOOK:
    raise RuntimeError("DISCORD_WEBHOOK env var not set")

# Polling interval in seconds (e.g., 3600 = 1 hour)
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))
# File to persist seen identifiers across restarts
STATE_FILE = os.environ.get("STATE_FILE", "seen_entries.json")
# User-Agent header for SEC requests
USER_AGENT = os.environ.get("USER_AGENT", "SEC WATCHER")

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s")

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
    """
    Fetch recent filings for a given CIK from the SEC submissions JSON.
    Returns a list of tuples: (accession, form, date, primary_document)
    """
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    recent = r.json().get("filings", {}).get("recent", {})
    return list(zip(
        recent.get("accessionNumber", []),
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("primaryDocument", [])
    ))


def fetch_rss_entries():
    """Parse the EDGAR RSS feed and return all entries"""
    feed = feedparser.parse(EDGAR_RSS_URL)
    return feed.entries


def extract_offering_details(text):
    """
    Simple regex-based extraction of offering size and ticker symbol from filing text.
    Returns a dict with keys 'amount' and 'ticker' if found.
    """
    details = {}
    # Attempt to find maximum aggregate offering amount
    m_amt = re.search(r"maximum\s+aggregate\s+offering\s+price\s*[:\-]?\s*\$?([0-9,]+\.?[0-9]*)", text, re.IGNORECASE)
    if m_amt:
        details['Amount'] = m_amt.group(1)
    # Attempt to find proposed ticker symbol
    m_tkr = re.search(r"proposed\s+ticker\s+symbol\s*[:\-]?\s*([A-Z]{1,5})", text, re.IGNORECASE)
    if m_tkr:
        details['Ticker'] = m_tkr.group(1)
    return details


def post_embed_to_discord(title, description, url, fields=None):
    """
    Send a Discord embed via webhook for a concise, structured notification.
    """
    embed = {
        "title": title,
        "description": description,
        "url": url,
        "timestamp": datetime.utcnow().isoformat(),
        "fields": []
    }
    if fields:
        for name, value in fields.items():
            embed['fields'].append({"name": name, "value": value, "inline": True})
    payload = {"embeds": [embed]}
    resp = requests.post(WEBHOOK, json=payload)
    try:
        resp.raise_for_status()
    except Exception as e:
        logging.error("Failed to post to Discord: %s", e)

# ------------------------------------------------------------
# 4. NOTIFICATION FUNCTIONS
# ------------------------------------------------------------

def handle_company_filing(name, acc, form, date, doc):
    """
    Post a structured embed for a company filing, extracting key details.
    """
    path = acc.replace('-', '')
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{CIKS[name]}/{path}/{doc}"
    # Fetch first 1000 chars for parsing details
    r = requests.get(filing_url.replace('.txt', '.txt'), headers={"User-Agent": USER_AGENT}, timeout=10)
    snippet = r.text[:2000]
    details = extract_offering_details(snippet)

    title = f"{name} filed {form}"
    desc = f"Date: {date}"
    fields = details
    post_embed_to_discord(title, desc, filing_url, fields)
    logging.info("Notified: %s %s", name, form)


def handle_industry_entry(entry):
    """
    Post a concise embed for an industry RSS filing entry.
    """
    title = entry.title
    link = entry.link
    # Use a short summary snippet
    summary = entry.summary.replace('\n', ' ')
    snippet = (summary[:200] + '...') if len(summary) > 200 else summary
    description = snippet
    post_embed_to_discord(title, description, link)
    logging.info("Industry post: %s", title)

# ------------------------------------------------------------
# 5. MAIN LOOP
# ------------------------------------------------------------

def main():
    while True:
        try:
            # Company-specific IPO filings
            for name, cik in CIKS.items():
                for acc, form, date, doc in fetch_filings(cik):
                    key = ("CIK", name, acc)
                    if key in seen:
                        continue
                    if form in ("S-1", "F-1", "D-1"):
                        handle_company_filing(name, acc, form, date, doc)
                    seen.add(key)

            # Industry filings via RSS
            for entry in fetch_rss_entries():
                key = ("RSS", entry.id)
                if key in seen:
                    continue
                text = (entry.title + entry.summary).lower()
                if any(k in text for k in RSS_KEYWORDS):
                    handle_industry_entry(entry)
                seen.add(key)

            save_state()
        except Exception as e:
            logging.exception("Error in main loop: %s", e)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
