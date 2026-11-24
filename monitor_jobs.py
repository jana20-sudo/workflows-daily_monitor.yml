import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress warnings if a legacy .gov.in SSL certificate requires verify=False fallback
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = Path("seen_jobs.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Verified official portals
PORTALS = [
    {
        "name": "FSSAI",
        "url": "https://www.fssai.gov.in/jobs-at-fssai",
        "category": "Food Tech",
    },
    {
        "name": "Central Warehousing Corp (CWC)",
        "url": "https://cwceportal.com/careers",
        "category": "Food Tech / PSU",
    },
    {
        "name": "Tea Board of India",
        "url": "https://www.teaboard.gov.in/",
        "category": "Food Tech / Commodity Board",
    },
    {
        "name": "Coffee Board of India",
        "url": "https://coffeeboard.gov.in/News.aspx",
        "category": "Food Tech / Commodity Board",
    },
    {
        "name": "Spices Board India",
        "url": "https://www.indianspices.com/",
        "category": "Food Tech / Commodity Board",
    },
    {
        "name": "SSC (Notice Board)",
        "url": "https://ssc.gov.in/home/notice-board",
        "category": "General Govt",
    },
    {
        "name": "RRB (Apply Portal)",
        "url": "http://www.rrbapply.gov.in/",
        "category": "General Govt",
    },
    {
        "name": "Employment News",
        "url": "https://employmentnews.gov.in/",
        "category": "Gazette Backup",
    },
]

# Bucket A: Food Technology & allied technical roles
FOOD_TECH_KEYWORDS = [
    "food", "fssai", "quality", "qa", "qc", "lab", "laboratory",
    "analyst", "technical", "warehouse", "storage", "tea", "coffee",
    "spice", "spices", "nutrition", "biotech", "microbiology",
    "chemistry", "agriculture", "agri", "factory advisory",
]

# Bucket B: General PSU/Govt, Degree, and Pre-Degree/Trainee roles
GENERAL_ELIGIBLE_KEYWORDS = [
    "recruit", "vacancy", "vacancies", "career", "advertisement",
    "advt", "notification", "notice", "trainee", "management trainee",
    "apprentice", "intern", "internship", "fellow", "project assistant",
    "graduate", "degree", "cgl", "chsl", "ntpc", "10+2",
    "appearing", "final year", "engagement", "contractual", "walk-in",
]


def fetch_html(url: str, retries: int = 2) -> str:
    """Fetch page HTML with retries and SSL fallback for legacy .gov.in certs."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.SSLError:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_err = exc
        except Exception as exc:
            last_err = exc
        time.sleep(2 * attempt)
    raise RuntimeError(f"Failed after {retries} attempts: {last_err}")


def classify_title(title: str) -> str:
    """Tag a posting into Food Tech, General/Pre-Degree, or Skip."""
    lower = title.lower()
    is_food = any(kw in lower for kw in FOOD_TECH_KEYWORDS)
    is_job = any(kw in lower for kw in GENERAL_ELIGIBLE_KEYWORDS)

    if is_food and is_job:
        return "FOOD_TECH"
    if is_food:
        return "FOOD_TECH"
    if is_job:
        return "GENERAL"
    return ""


def extract_postings(portal: Dict[str, str], html: str) -> List[Dict[str, str]]:
    """Extract relevant job/notice links from a portal's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []
    seen_urls = set()

    for tag in soup.find_all("a", href=True):
        raw_text = " ".join(tag.get_text(" ", strip=True).split())
        href = tag["href"].strip()

        if not raw_text or len(raw_text) < 8:
            continue
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        full_url = urljoin(portal["url"], href)
        if full_url in seen_urls:
            continue

        bucket = classify_title(raw_text)
        # Also include direct PDF links on dedicated career pages
        if not bucket and full_url.lower().endswith(".pdf") and portal["name"] in {
            "FSSAI", "Central Warehousing Corp (CWC)", "SSC (Notice Board)"
        }:
            bucket = "GENERAL"

        if bucket:
            seen_urls.add(full_url)
            item_id = hashlib.sha256(f"{portal['name']}|{full_url}|{raw_text}".encode()).hexdigest()[:16]
            results.append(
                {
                    "id": item_id,
                    "portal": portal["name"],
                    "bucket": bucket,
                    "title": raw_text[:180],
                    "url": full_url,
                }
            )
    return results


def load_seen() -> Dict[str, dict]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_seen(seen: Dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    """Send a Telegram message with automatic 429 retry_after handling."""
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks: List[str] = []
    while len(text) > 4000:
        split_idx = text.rfind("\n", 0, 4000)
        if split_idx == -1:
            split_idx = 4000
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip()
    chunks.append(text)

    for chunk in chunks:
        for attempt in range(1, 6):
            try:
                resp = requests.post(
                    api_url,
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=45,
                )
                if resp.status_code == 429:
                    retry_after = 15
                    try:
                        data = resp.json()
                        retry_after = int(data.get("parameters", {}).get("retry_after", 15))
                    except Exception:
                        pass
                    wait_sec = min(max(retry_after + 2, 5), 65)
                    print(f"Telegram 429 rate limit (attempt {attempt}/5). Body: {resp.text}. Waiting {wait_sec}s...")
                    time.sleep(wait_sec)
                    continue
                if not resp.ok:
                    print(f"Telegram API error {resp.status_code}: {resp.text}")
                    resp.raise_for_status()
                print("Telegram message sent successfully.")
                break
            except Exception as exc:
                if attempt == 5:
                    raise
                wait_sec = 5 * attempt
                print(f"Telegram send error (attempt {attempt}/5): {exc}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)
        else:
            raise RuntimeError("Failed to send Telegram message after 5 attempts due to rate limiting (429).")


def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise EnvironmentError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")

    seen = load_seen()
    first_run = len(seen) == 0
    now_ist = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    new_food_tech: List[Dict[str, str]] = []
    new_general: List[Dict[str, str]] = []
    failed_portals: List[Tuple[str, str]] = []
    ok_count = 0

    for portal in PORTALS:
        try:
            html = fetch_html(portal["url"])
            postings = extract_postings(portal, html)
            ok_count += 1
            for item in postings:
                if item["id"] not in seen:
                    seen[item["id"]] = {
                        "portal": item["portal"],
                        "title": item["title"],
                        "url": item["url"],
                        "first_seen": now_ist,
                    }
                    if not first_run:
                        if item["bucket"] == "FOOD_TECH":
                            new_food_tech.append(item)
                        else:
                            new_general.append(item)
        except Exception as exc:
            failed_portals.append((portal["name"], str(exc)))

    save_seen(seen)

    lines = [f"Govt & PSU Job Monitor ({now_ist})"]
    lines.append(f"Checked: {ok_count}/{len(PORTALS)} portals")

    if first_run:
        lines.append(
            f"\nBaseline initialized with {len(seen)} existing links. "
            "Starting tomorrow, you will receive alerts for every new posting."
        )
    elif not new_food_tech and not new_general:
        lines.append("\nNo new postings today. Monitor is active and healthy.")
    else:
        if new_food_tech:
            lines.append(f"\n[FOOD TECH / ALLIED] ({len(new_food_tech)} new)")
            for item in new_food_tech[:25]:
                lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")

        if new_general:
            lines.append(f"\n[GENERAL GOVT / DEGREE / TRAINEE] ({len(new_general)} new)")
            for item in new_general[:25]:
                lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:120]}")

    digest = "\n".join(lines)
    print(digest)
    send_telegram_message(token, chat_id, digest)


if __name__ == "__main__":
    run()
