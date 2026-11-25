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

# Verified Indian Govt, Food Tech, PSU, and International Food Safety portals
PORTALS = [
    # --- Indian Food Tech & Statutory Boards ---
    {
        "name": "FSSAI",
        "url": "https://www.fssai.gov.in/jobs-at-fssai",
        "category": "India Food Tech",
        "dedicated_jobs_page": True,
    },
    {
        "name": "Central Warehousing Corp (CWC)",
        "url": "https://cwceportal.com/careers",
        "category": "India Food Tech",
        "dedicated_jobs_page": True,
    },
    {
        "name": "Food Corporation of India (FCI)",
        "url": "https://fci.gov.in/",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    {
        "name": "Bureau of Indian Standards (BIS)",
        "url": "https://www.bis.gov.in/",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    {
        "name": "CSIR-CFTRI (Regular)",
        "url": "https://cftri.res.in/recruitments/regular-positions",
        "category": "India Food Tech",
        "dedicated_jobs_page": True,
    },
    {
        "name": "CSIR-CFTRI (Project Staff)",
        "url": "https://cftri.res.in/recruitments/project-positions",
        "category": "India Food Tech",
        "dedicated_jobs_page": True,
    },
    {
        "name": "Tea Board of India",
        "url": "https://www.teaboard.gov.in/",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    {
        "name": "Coffee Board of India",
        "url": "https://coffeeboard.gov.in/News.aspx",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    {
        "name": "Spices Board India",
        "url": "https://www.indianspices.com/",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    {
        "name": "APEDA",
        "url": "https://apeda.gov.in/",
        "category": "India Food Tech",
        "dedicated_jobs_page": False,
    },
    # --- General Indian Govt & Railways ---
    {
        "name": "SSC (Notice Board)",
        "url": "https://ssc.gov.in/home/notice-board",
        "category": "Govt & RRB",
        "dedicated_jobs_page": True,
    },
    {
        "name": "RRB Chennai",
        "url": "https://www.rrbchennai.gov.in/",
        "category": "Govt & RRB",
        "dedicated_jobs_page": True,
    },
    # --- International Food Safety & UN Food Agencies ---
    {
        "name": "FAO (UN Food & Agriculture)",
        "url": "https://www.fao.org/employment/en",
        "category": "International",
        "dedicated_jobs_page": True,
    },
    {
        "name": "FAO Internships (Freshers)",
        "url": "https://www.fao.org/employment/young-talent-programme/internship-programme/en",
        "category": "International",
        "dedicated_jobs_page": True,
    },
    {
        "name": "EFSA (European Food Safety)",
        "url": "https://careers.efsa.europa.eu/",
        "category": "International",
        "dedicated_jobs_page": True,
    },
    {
        "name": "EFSA Young Professionals",
        "url": "https://careers.efsa.europa.eu/youngprofessionals",
        "category": "International",
        "dedicated_jobs_page": True,
    },
    {
        "name": "WFP (UN World Food Programme)",
        "url": "https://www.wfp.org/careers",
        "category": "International",
        "dedicated_jobs_page": True,
    },
]

# Immediately ignore auctions, tenders, market prices, and non-job administrative notices
NOISE_KEYWORDS = [
    "auction", "e-auction", "auctioneer", "qty arrived", "no.of lots",
    "small cardamom", "large cardamom", "daily price", "market price",
    "tender", "e-tender", "quotation", "rfp", "rfq", "bid inviting",
    "online bid", "bidding", "supply and commissioning", "procurement",
    "export permit", "subsidy", "rcmc", "photo gallery", "annual report",
    "holiday list", "press release", "parliament", "citizen charter",
]

# Bucket A: Food Technology, Food Safety & International Agencies
FOOD_TECH_KEYWORDS = [
    "food", "fssai", "efsa", "fao", "wfp", "codex", "haccp", "iso",
    "quality", "qa", "qc", "lab", "laboratory", "analyst", "technical",
    "warehouse", "storage", "nutrition", "biotech", "microbiology",
    "chemistry", "agriculture", "agri", "factory advisory", "scientist",
    "research", "safety",
]

# Bucket B: Explicit job, recruitment, exam, internship, and trainee signals
JOB_SIGNAL_KEYWORDS = [
    "recruit", "vacancy", "vacancies", "career", "careers", "job", "jobs",
    "employment", "advertisement", "advt", "post of", "posts of", "position",
    "trainee", "traineeship", "management trainee", "apprentice", "intern",
    "internship", "fellow", "fellowship", "project associate", "project assistant",
    "young professional", "graduate", "fresher", "junior", "cgl", "chsl",
    "ntpc", "10+2", "appearing", "final year", "engagement", "contractual",
    "walk-in", "interview", "selection post", "call for", "expression of interest",
    "applications are invited", "apply online",
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


def classify_title(portal: Dict[str, object], title: str) -> str:
    """Tag a posting into INTERNATIONAL, FOOD_TECH, GENERAL, or Skip (blocking noise)."""
    lower = title.lower()

    # Drop auctions, tenders, market prices, and non-job notices immediately
    if any(noise in lower for noise in NOISE_KEYWORDS):
        return ""

    is_food = any(kw in lower for kw in FOOD_TECH_KEYWORDS)
    is_job = any(kw in lower for kw in JOB_SIGNAL_KEYWORDS)

    # General board homepages (Spices/Tea/Coffee/FCI/BIS/APEDA) MUST contain a job signal
    if not portal.get("dedicated_jobs_page", False) and not is_job:
        return ""

    if portal["category"] == "International" and (is_food or is_job):
        return "INTERNATIONAL"
    if is_food and is_job:
        return "FOOD_TECH"
    if portal["category"] == "India Food Tech" and is_job:
        return "FOOD_TECH"
    if portal.get("dedicated_jobs_page", False) and is_food:
        return "FOOD_TECH"
    if is_job:
        return "GENERAL"
    return ""


def extract_postings(portal: Dict[str, object], page_html: str) -> List[Dict[str, str]]:
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

        full_url = urljoin(str(portal["url"]), href)
        if full_url in seen_urls:
            continue

        bucket = classify_title(portal, raw_text)
        # Allow direct PDF notices on dedicated career portals (unless blocked by NOISE_KEYWORDS)
        if (
            not bucket
            and full_url.lower().endswith(".pdf")
            and portal.get("dedicated_jobs_page", False)
            and not any(noise in raw_text.lower() for noise in NOISE_KEYWORDS)
        ):
            bucket = "FOOD_TECH" if portal["category"] == "India Food Tech" else "GENERAL"

        if bucket:
            seen_urls.add(full_url)
            item_id = hashlib.sha256(f"{portal['name']}|{full_url}|{raw_text}".encode()).hexdigest()[:16]
            results.append(
                {
                    "id": item_id,
                    "portal": str(portal["name"]),
                    "category": str(portal["category"]),
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
    new_intl: List[Dict[str, str]],
    new_food_tech: List[Dict[str, str]],
    new_general: List[Dict[str, str]],
    failed_portals: List[Tuple[str, str]],
) -> str:
    parts = [
        "<div style='font-family: Arial, sans-serif; color: #222; line-height: 1.5;'>",
        "<h2 style='margin-bottom: 4px;'>Global Food Tech &amp; Govt Job Monitor</h2>",
        f"<p style='color: #555; margin-top: 0;'>{html_lib.escape(now_ist)} &middot; Checked {ok_count}/{total_portals} portals</p>",
    ]

    if first_run:
        parts.append(
            f"<p><strong>Baseline initialized</strong> with {total_seen} existing links. "
            "Starting tomorrow at 8:00 AM IST, you will receive an email whenever new postings appear.</p>"
        )
    elif not new_intl and not new_food_tech and not new_general:
        parts.append("<p>No new postings today. Your monitor is active and healthy.</p>")
    else:
        for heading, color, items in [
            ("International Food Safety &amp; Freshers", "#6a1b9a", new_intl),
            ("India Food Tech &amp; Allied", "#0b5394", new_food_tech),
            ("General Govt / RRB / Degree / Trainee", "#38761d", new_general),
        ]:
            if items:
                parts.append(f"<h3 style='color: {color};'>{heading} ({len(items)} new)</h3><ul>")
                for item in items[:35]:
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

    new_intl: List[Dict[str, str]] = []
    new_food_tech: List[Dict[str, str]] = []
    new_general: List[Dict[str, str]] = []
    failed_portals: List[Tuple[str, str]] = []
    ok_count = 0

    for portal in PORTALS:
        try:
            page_html = fetch_html(str(portal["url"]))
            postings = extract_postings(portal, page_html)
            ok_count += 1
            for item in postings:
                if item["id"] not in seen:
                    seen[item["id"]] = {
                        "portal": item["portal"],
                        "category": item["category"],
                        "bucket": item["bucket"],
                        "title": item["title"],
                        "url": item["url"],
                        "first_seen": now_ist,
                    }
                    if not first_run:
                        if item["bucket"] == "INTERNATIONAL":
                            new_intl.append(item)
                        elif item["bucket"] == "FOOD_TECH":
                            new_food_tech.append(item)
                        else:
                            new_general.append(item)
        except Exception as exc:
            failed_portals.append((str(portal["name"]), str(exc)))

    save_seen(seen)

    lines = [f"Global Food Tech & Govt Job Monitor ({now_ist})", f"Checked: {ok_count}/{len(PORTALS)} portals"]
    total_new = len(new_intl) + len(new_food_tech) + len(new_general)

    if first_run:
        subject = f"[Job Monitor] Baseline Initialized ({len(seen)} links tracked)"
        lines.append(
            f"\nBaseline initialized with {len(seen)} existing links across Indian & International portals."
        )
    elif total_new == 0:
        subject = f"[Job Monitor] 0 New Postings Today ({short_date})"
        lines.append("\nNo new postings today. Monitor is active and healthy.")
    else:
        noun = "Posting" if total_new == 1 else "Postings"
        subject = f"[Job Monitor] {total_new} New {noun} Found ({short_date})"
        for label, items in [
            ("INTERNATIONAL FOOD SAFETY / FRESHERS", new_intl),
            ("INDIA FOOD TECH / ALLIED", new_food_tech),
            ("GENERAL GOVT / RRB / TRAINEE", new_general),
        ]:
            if items:
                lines.append(f"\n[{label}] ({len(items)} new)")
                for item in items[:35]:
                    lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:150]}")

    text_body = "\n".join(lines)
    html_body = build_html_body(
        now_ist, ok_count, len(PORTALS), first_run, len(seen), new_intl, new_food_tech, new_general, failed_portals
    )

    print(text_body)
    send_gmail(gmail_address, gmail_app_password, recipient_email, subject, text_body, html_body)


if __name__ == "__main__":
    run()"""
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

    new_intl: List[Dict[str, str]] = []
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
                        "category": item["category"],
                        "bucket": item["bucket"],
                        "title": item["title"],
                        "url": item["url"],
                        "first_seen": now_ist,
                    }
                    if not first_run:
                        if item["bucket"] == "INTERNATIONAL":
                            new_intl.append(item)
                        elif item["bucket"] == "FOOD_TECH":
                            new_food_tech.append(item)
                        else:
                            new_general.append(item)
        except Exception as exc:
            failed_portals.append((portal["name"], str(exc)))

    save_seen(seen)

    lines = [f"Global Food Tech & Govt Job Monitor ({now_ist})", f"Checked: {ok_count}/{len(PORTALS)} portals"]
    total_new = len(new_intl) + len(new_food_tech) + len(new_general)

    if first_run:
        subject = f"[Job Monitor] Baseline Initialized ({len(seen)} links tracked)"
        lines.append(
            f"\nBaseline initialized with {len(seen)} existing links across Indian & International portals."
        )
    elif total_new == 0:
        subject = f"[Job Monitor] 0 New Postings Today ({short_date})"
        lines.append("\nNo new postings today. Monitor is active and healthy.")
    else:
        subject = f"[Job Monitor] {total_new} New Postings Found ({short_date})"
        for label, items in [
            ("INTERNATIONAL FOOD SAFETY / FRESHERS", new_intl),
            ("INDIA FOOD TECH / ALLIED", new_food_tech),
            ("GENERAL GOVT / RRB / TRAINEE", new_general),
        ]:
            if items:
                lines.append(f"\n[{label}] ({len(items)} new)")
                for item in items[:35]:
                    lines.append(f"- {item['portal']}: {item['title']}\n  {item['url']}")

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:150]}")

    text_body = "\n".join(lines)
    html_body = build_html_body(
        now_ist, ok_count, len(PORTALS), first_run, len(seen), new_intl, new_food_tech, new_general, failed_portals
    )

    print(text_body)
    send_gmail(gmail_address, gmail_app_password, recipient_email, subject, text_body, html_body)


if __name__ == "__main__":
    run()
