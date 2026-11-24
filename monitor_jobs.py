import hashlib
import html as html_lib
import json
import os
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
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

    if is_food:
        return "FOOD_TECH"
    if is_job:
        return "GENERAL"
    return ""


def extract_postings(portal: Dict[str, str], page_html: str) -> List[Dict[str, str]]:
    """Extract relevant job/notice links from a portal's HTML."""
    soup = BeautifulSoup(page_html, "html.parser")
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


def build_html_body(
    now_ist: str,
    ok_count: int,
    total_portals: int,
    first_run: bool,
    total_seen: int,
    new_food_tech: List[Dict[str, str]],
    new_general: List[Dict[str, str]],
    failed_portals: List[Tuple[str, str]],
) -> str:
    parts = [
        "<div style='font-family: Arial, sans-serif; color: #222; line-height: 1.5;'>",
        f"<h2 style='margin-bottom: 4px;'>Govt &amp; PSU Job Monitor</h2>",
        f"<p style='color: #555; margin-top: 0;'>{html_lib.escape(now_ist)} &middot; Checked {ok_count}/{total_portals} portals</p>",
    ]

    if first_run:
        parts.append(
            f"<p><strong>Baseline initialized</strong> with {total_seen} existing links. "
            "Starting tomorrow at 8:00 AM IST, you will receive an email whenever new postings appear.</p>"
        )
    elif not new_food_tech and not new_general:
        parts.append("<p>No new postings today. Your monitor is active and healthy.</p>")
    else:
        if new_food_tech:
            parts.append(f"<h3 style='color: #0b5394;'>Food Tech &amp; Allied ({len(new_food_tech)} new)</h3><ul>")
            for item in new_food_tech[:40]:
                portal = html_lib.escape(item["portal"])
                title = html_lib.escape(item["title"])
                url = html_lib.escape(item["url"], quote=True)
                parts.append(f"<li><strong>{portal}:</strong> <a href='{url}'>{title}</a></li>")
            parts.append("</ul>")

        if new_general:
            parts.append(f"<h3 style='color: #38761d;'>General Govt / Degree / Trainee ({len(new_general)} new)</h3><ul>")
            for item in new_general[:40]:
                portal = html_lib.escape(item["portal"])
                title = html_lib.escape(item["title"])
                url = html_lib.escape(item["url"], quote=True)
                parts.append(f"<li><strong>{portal}:</strong> <a href='{url}'>{title}</a></li>")
            parts.append("</ul>")

    if failed_portals:
        parts.append("<h3 style='color: #b45f06;'>Portal Warnings</h3><ul>")
        for name, err in failed_portals:
            parts.append(f"<li><strong>{html_lib.escape(name)}:</strong> {html_lib.escape(err[:150])}</li>")
        parts.append("</ul>")

    parts.append("</div>")
    return "".join(parts)


def send_gmail(sender: str, app_password: str, recipient: str, subject: str, text_body: str, html_body: str) -> None:
    """Send multipart plain-text + HTML email via Gmail SMTP_SSL."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    clean_password = app_password.replace(" ", "").strip()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, clean_password)
        server.send_message(msg)
    print(f"Email sent successfully to {recipient}.")


def run() -> None:
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient_email = os.environ.get("RECIPIENT_EMAIL", "").strip() or gmail_address

    if not gmail_address or not gmail_app_password:
        raise EnvironmentError("Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD environment variables.")

    seen = load_seen()
    first_run = len(seen) == 0
    now_dt = datetime.now(IST)
    now_ist = now_dt.strftime("%d %b %Y, %I:%M %p IST")
    short_date = now_dt.strftime("%d %b %Y")

    new_food_tech: List[Dict[str, str]] = []
    new_general: List[Dict[str, str]] = []
    failed_portals: List[Tuple[str, str]] = []
    ok_count = 0

    for portal in PORTALS:
        try:
            page_html = fetch_html(portal["url"])
            postings = extract_postings(portal, page_html)
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

    lines = [f"Govt & PSU Job Monitor ({now_ist})", f"Checked: {ok_count}/{len(PORTALS)} portals"]
    total_new = len(new_food_tech) + len(new_general)

    if first_run:
        subject = f"[Govt Job Monitor] Baseline Initialized ({len(seen)} links tracked)"
        lines.append(
            f"\nBaseline initialized with {len(seen)} existing links. "
            "Starting tomorrow at 8:00 AM IST, you will receive alerts for every new posting."
        )
    elif total_new == 0:
        subject = f"[Govt Job Monitor] 0 New Postings Today ({short_date})"
        lines.append("\nNo new postings today. Monitor is active and healthy.")
    else:
        subject = f"[Govt Job Monitor] {total_new} New Postings Found ({short_date})"
        if new_food_tech:
            lines.append(f"\n[FOOD TECH / ALLIED] ({len(new_food_tech)} new)")
            for item in new_food_tech[:40]:
                lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")
        if new_general:
            lines.append(f"\n[GENERAL GOVT / DEGREE / TRAINEE] ({len(new_general)} new)")
            for item in new_general[:40]:
                lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:150]}")

    text_body = "\n".join(lines)
    html_body = build_html_body(
        now_ist, ok_count, len(PORTALS), first_run, len(seen), new_food_tech, new_general, failed_portals
    )

    print(text_body)
    send_gmail(gmail_address, gmail_app_password, recipient_email, subject, text_body, html_body)


if __name__ == "__main__":
    run()               

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:120]}")

    digest = "\n".join(lines)
    print(digest)
    send_telegram_message(token, chat_id, digest)


if __name__ == "__main__":
    run()
