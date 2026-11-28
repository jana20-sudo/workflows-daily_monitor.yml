#!/usr/bin/env python3
"""Global Food Tech & Govt Job Monitor (v2).

Features
- Word-boundary keyword matching and URL-only IDs (fewer false / repeat alerts)
- Flavor/ingredient, academia/PhD and India R&D portals; RSS / Greenhouse / Lever feed support
- Optional Playwright rendering for JavaScript-heavy sites
- Relevance scoring with a "Top picks" section
- Deadline parsing (titles + PDFs) and "closing soon" reminders
- Email digest + optional Telegram push + Sunday weekly summary
- Retry session, SSL-fallback logging, zero-link health checks, state pruning
- Optional Claude relevance filter and one-line summaries
"""
from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import os
import re
import smtplib
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen_jobs.json"))
PRUNE_DAYS = 180
DEADLINE_WARN_DAYS = 3
ZERO_STREAK_WARN = 3
TOP_SCORE = 5
TG_MIN_SCORE = int(os.environ.get("TG_MIN_SCORE", TOP_SCORE))
MAX_PER_SECTION = 30
AI_MAX_ITEMS = 40
PDF_LOOKUPS_PER_RUN = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CANDIDATE_PROFILE = os.environ.get(
    "CANDIDATE_PROFILE",
    "Final-year B.Tech Food Technology fresher (data science minor) targeting flavor/fragrance, "
    "encapsulation, food process optimization, food safety/regulatory roles, and MSc/PhD study abroad.",
)


def P(name: str, url: str, category: str, dedicated: bool = True, **extra) -> Dict[str, object]:
    return {"name": name, "url": url, "category": category, "dedicated": dedicated, **extra}


# NOTE: URLs below were written from memory. Any that are wrong or blocked will
# show up under "Portal Warnings" (or the zero-link health check) in your email.
PORTALS: List[Dict[str, object]] = [
    # --- India food tech / govt ---
    P("FSSAI", "https://www.fssai.gov.in/jobs-at-fssai", "India Food Tech"),
    P("Central Warehousing Corp (CWC)", "https://cwceportal.com/careers", "India Food Tech"),
    P("Food Corporation of India (FCI)", "https://fci.gov.in/", "India Food Tech", dedicated=False),
    P("Bureau of Indian Standards (BIS)", "https://www.bis.gov.in/", "India Food Tech", dedicated=False),
    P("CSIR-CFTRI (Regular)", "https://cftri.res.in/recruitments/regular-positions", "India Food Tech"),
    P("CSIR-CFTRI (Project Staff)", "https://cftri.res.in/recruitments/project-positions", "India Food Tech"),
    P("CSIR-CLRI", "https://clri.org/", "India Food Tech", dedicated=False),
    P("NIFTEM Kundli", "https://www.niftem.ac.in/", "India Food Tech", dedicated=False),
    P("NIFTEM Thanjavur", "https://niftem-t.ac.in/", "India Food Tech", dedicated=False),
    P("IIFPT Thanjavur", "https://iifpt.edu.in/", "India Food Tech", dedicated=False),
    P("Tea Board of India", "https://www.teaboard.gov.in/", "India Food Tech", dedicated=False),
    P("Coffee Board of India", "https://coffeeboard.gov.in/News.aspx", "India Food Tech", dedicated=False),
    P("Spices Board India", "https://www.indianspices.com/", "India Food Tech", dedicated=False),
    P("APEDA", "https://apeda.gov.in/", "India Food Tech", dedicated=False),
    P("Anna University", "https://www.annauniv.edu/", "India Food Tech", dedicated=False),
    P("SSC (Notice Board)", "https://ssc.gov.in/home/notice-board", "Govt & RRB"),
    P("RRB Chennai", "https://www.rrbchennai.gov.in/", "Govt & RRB"),
    # --- International bodies ---
    P("FAO (UN Food & Agriculture)", "https://www.fao.org/employment/en", "International"),
    P("FAO Internships (Freshers)", "https://www.fao.org/employment/young-talent-programme/internship-programme/en", "International"),
    P("EFSA (European Food Safety)", "https://careers.efsa.europa.eu/", "International", js=True),
    P("EFSA Young Professionals", "https://careers.efsa.europa.eu/youngprofessionals", "International", js=True),
    P("WFP (UN World Food Programme)", "https://www.wfp.org/careers", "International", js=True),
    # --- Flavor, fragrance & ingredient companies ---
    P("Symrise", "https://www.symrise.com/careers/", "Flavor & Ingredients", js=True),
    P("IFF", "https://jobs.iff.com/", "Flavor & Ingredients", js=True),
    P("Kemin", "https://www.kemin.com/na/en-us/about/careers", "Flavor & Ingredients", js=True),
    P("Givaudan", "https://www.givaudan.com/careers", "Flavor & Ingredients", js=True),
    P("dsm-firmenich", "https://www.dsm-firmenich.com/corporate/careers.html", "Flavor & Ingredients", js=True),
    P("Sensient", "https://sensient.com/careers/", "Flavor & Ingredients", js=True),
    P("Mane", "https://www.mane.com/careers", "Flavor & Ingredients", js=True),
    P("Takasago", "https://www.takasago.com/en/careers/", "Flavor & Ingredients", js=True),
    # --- Postgraduate / PhD ---
    P("WUR Vacancies", "https://www.wur.nl/en/vacancies.htm", "Academia & PhD", js=True),
    P("WUR PhD Positions", "https://www.wur.nl/en/vacancies/phd-positions.htm", "Academia & PhD", js=True),
    P("EURAXESS (food science)", "https://euraxess.ec.europa.eu/jobs/search?keywords=food%20science", "Academia & PhD", js=True),
    P("FindAPhD (food science)", "https://www.findaphd.com/phds/food-science/", "Academia & PhD"),
]

# Companies on Greenhouse / Lever have clean JSON feeds. Add them here:
#   GREENHOUSE_BOARDS = {"Some Company": "board_token"}   # boards-api.greenhouse.io/v1/boards/<token>/jobs
#   LEVER_BOARDS = {"Some Company": "company_slug"}       # api.lever.co/v0/postings/<slug>?mode=json
GREENHOUSE_BOARDS: Dict[str, str] = {}
LEVER_BOARDS: Dict[str, str] = {}
for _name, _token in GREENHOUSE_BOARDS.items():
    PORTALS.append(P(_name, f"https://boards-api.greenhouse.io/v1/boards/{_token}/jobs", "Flavor & Ingredients", kind="greenhouse"))
for _name, _slug in LEVER_BOARDS.items():
    PORTALS.append(P(_name, f"https://api.lever.co/v0/postings/{_slug}?mode=json", "Flavor & Ingredients", kind="lever"))

CATEGORY_ORDER = ["Flavor & Ingredients", "Academia & PhD", "International", "India Food Tech", "Govt & RRB"]
CATEGORY_COLORS = {
    "Flavor & Ingredients": "#c2185b",
    "Academia & PhD": "#00695c",
    "International": "#6a1b9a",
    "India Food Tech": "#0b5394",
    "Govt & RRB": "#38761d",
}

# --------------------------------------------------------------------------
# Keyword matching (regex with word boundaries)
# --------------------------------------------------------------------------
def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(patterns), re.I)


NOISE_RX = _rx(
    r"\be-?auctions?\b", r"\bauctioneer\b", r"qty arrived", r"no\.?\s?of lots",
    r"\bsmall cardamom\b", r"\blarge cardamom\b", r"\bdaily price\b", r"\bmarket price\b",
    r"\be-?tenders?\b", r"\btenders?\b", r"\bquotations?\b", r"\brfp\b", r"\brfq\b",
    r"\bbid inviting\b", r"\bonline bid\b", r"\bbidding\b", r"supply and commissioning",
    r"\bprocurement\b", r"\bexport permit\b", r"\bsubsidy\b", r"\brcmc\b",
    r"\bphoto gallery\b", r"\bannual report\b", r"\bholiday list\b", r"\bpress release\b",
    r"\bparliament\b", r"\bcitizen charter\b",
)

FOOD_RX = _rx(
    r"\bfood\b", r"\bfssai\b", r"\befsa\b", r"\bfao\b", r"\bwfp\b", r"\bcodex\b", r"\bhaccp\b",
    r"\biso\b", r"\bquality\b", r"\bqa\b", r"\bqc\b", r"\blab(?:oratory)?\b", r"\banalyst\b",
    r"\btechnical\b", r"\bwarehouse\b", r"\bstorage\b", r"\bnutrition", r"\bbiotech",
    r"\bmicrobiolog", r"\bchemistry\b", r"\bagri", r"\bscientist\b", r"\bresearch\b",
    r"\bsafety\b", r"\bflavou?r", r"\bfragrance", r"\bencapsulat", r"\bformulation",
)

JOB_RX = _rx(
    r"\brecruit", r"\bvacanc", r"\bcareers?\b", r"\bjobs?\b", r"\bemployment\b",
    r"\badvertisement\b", r"\badvt\b", r"\bposts? of\b", r"\bposition", r"\btrainee",
    r"\bapprentice", r"\bintern(?:ship)?s?\b", r"\bfellow", r"\bproject (?:associate|assistant|scientist)\b",
    r"\byoung professional", r"\bgraduate", r"\bfreshers?\b", r"\bjunior\b", r"\bcgl\b",
    r"\bchsl\b", r"\bntpc\b", r"10\+2", r"\bappearing\b", r"\bfinal year\b", r"\bengagement\b",
    r"\bcontractual\b", r"\bwalk-?in\b", r"\binterview\b", r"\bselection post\b", r"\bcall for\b",
    r"\bexpression of interest\b", r"\bapplications? (?:are )?invited\b", r"\bapply (?:online|now)\b",
    r"\bph\.?d\b", r"\bdoctoral\b", r"\bresearch assistant\b",
)

SCORE_RULES: List[Tuple[re.Pattern, int]] = [
    (_rx(r"\bflavou?r", r"\bfragrance", r"\baroma", r"\bencapsulat"), 3),
    (_rx(r"\bfood\s+(?:safety|technolog|science|process)"), 3),
    (_rx(r"\bfreshers?\b", r"\bgraduate\s+trainee", r"\bmanagement\s+trainee", r"\bentry[\s-]level"), 3),
    (_rx(r"\btrainee", r"\bintern(?:ship)?s?\b", r"\byoung\s+professional", r"\bgraduate",
         r"\bproject\s+(?:associate|assistant|scientist)\b", r"\bph\.?d\b", r"\bdoctoral\b",
         r"\bfellowship", r"\bjunior\b"), 2),
    (_rx(r"\br&d\b", r"\bprocess\s+optimi[sz]ation", r"\bregulatory\b", r"\bquality\b",
         r"\bhaccp\b", r"\bcodex\b", r"\bformulation"), 2),
    (_rx(r"\bdata\b", r"\bai\b", r"\bmachine\s+learning\b", r"\banalytics\b", r"\bresearch\b",
         r"\banalyst\b", r"\bfood\b"), 1),
    (_rx(r"\bsenior\b", r"\bdirector\b", r"\bvice\s+president\b", r"\bvp\b", r"\bprincipal\b",
         r"\bhead\s+of\b", r"\bmanager\b", r"\bexperienced\b"), -3),
]


def score_title(title: str) -> int:
    return sum(weight for rx, weight in SCORE_RULES if rx.search(title))


def is_pdf_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


def evaluate(portal: Dict[str, object], title: str, url: str) -> Optional[int]:
    """Return a relevance score, or None if the link should be ignored."""
    if NOISE_RX.search(title):
        return None
    is_food = bool(FOOD_RX.search(title))
    is_job = bool(JOB_RX.search(title))
    dedicated = bool(portal.get("dedicated", False))
    if not (is_job or (dedicated and (is_food or is_pdf_url(url)))):
        return None
    return score_title(title)


# --------------------------------------------------------------------------
# HTTP + rendering
# --------------------------------------------------------------------------
def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(total=2, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = make_session()
INSECURE_PORTALS: set = set()


def http_get(portal: Dict[str, object], url: Optional[str] = None) -> requests.Response:
    timeout = int(portal.get("timeout", 20))
    target = url or str(portal["url"])
    try:
        resp = SESSION.get(target, timeout=timeout)
    except requests.exceptions.SSLError:
        INSECURE_PORTALS.add(str(portal["name"]))
        resp = SESSION.get(target, timeout=timeout, verify=False)
    resp.raise_for_status()
    return resp


class Renderer:
    """Lazy Playwright wrapper for JavaScript-rendered pages."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self.disabled = False

    def render(self, url: str, timeout_ms: int = 30000) -> str:
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright

                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch()
            except Exception:
                self.disabled = True
                raise
        context = self._browser.new_context(user_agent=HEADERS["User-Agent"], ignore_https_errors=True)
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            context.close()

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def make_id(portal_name: str, url: str) -> str:
    return hashlib.sha256(f"{portal_name}|{url.strip()}".encode()).hexdigest()[:16]


def build_items(portal: Dict[str, object], rows: List[Tuple[str, str]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    seen_urls = set()
    for raw_title, url in rows:
        title = " ".join(raw_title.split())
        if len(title) < 8 or not url or url in seen_urls:
            continue
        score = evaluate(portal, title, url)
        if score is None:
            continue
        seen_urls.add(url)
        out.append({
            "id": make_id(str(portal["name"]), url),
            "portal": str(portal["name"]),
            "category": str(portal["category"]),
            "title": title[:180],
            "url": url,
            "score": score,
        })
    return out


def extract_postings(portal: Dict[str, object], page_html: str) -> List[Dict[str, object]]:
    soup = BeautifulSoup(page_html, "html.parser")
    rows: List[Tuple[str, str]] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        rows.append((tag.get_text(" ", strip=True), urljoin(str(portal["url"]), href)))
    return build_items(portal, rows)


def parse_feed(portal: Dict[str, object], resp: requests.Response) -> List[Dict[str, object]]:
    kind = portal["kind"]
    rows: List[Tuple[str, str]] = []
    if kind == "rss":
        root = ET.fromstring(resp.content)
        for node in root.iter():
            if node.tag.split("}")[-1] not in ("item", "entry"):
                continue
            title = link = ""
            for child in node:
                tag = child.tag.split("}")[-1]
                if tag == "title":
                    title = (child.text or "").strip()
                elif tag == "link":
                    link = (child.get("href") or child.text or "").strip()
            if title and link:
                rows.append((title, urljoin(str(portal["url"]), link)))
    elif kind == "greenhouse":
        for job in resp.json().get("jobs", []):
            loc = (job.get("location") or {}).get("name", "")
            title = job.get("title", "")
            rows.append((f"{title} ({loc})" if loc else title, job.get("absolute_url", "")))
    elif kind == "lever":
        for job in resp.json():
            loc = (job.get("categories") or {}).get("location", "")
            title = job.get("text", "")
            rows.append((f"{title} ({loc})" if loc else title, job.get("hostedUrl", "")))
    return build_items(portal, rows)


def fetch_postings(portal: Dict[str, object], renderer: Renderer, notes: List[str]) -> List[Dict[str, object]]:
    if portal.get("kind", "html") in ("rss", "greenhouse", "lever"):
        return parse_feed(portal, http_get(portal))

    page_html: Optional[str] = None
    if portal.get("js") and not renderer.disabled:
        try:
            page_html = renderer.render(str(portal["url"]))
        except ImportError:
            renderer.disabled = True
            notes.append("Playwright not installed: JS-heavy portals fall back to static HTML.")
        except Exception as exc:
            if renderer.disabled:
                notes.append(f"Playwright unavailable ({type(exc).__name__}): JS portals use static HTML.")
            else:
                notes.append(f"{portal['name']}: browser render failed ({type(exc).__name__}); used static HTML.")
    if page_html is None:
        page_html = http_get(portal).text
    return extract_postings(portal, page_html)


# --------------------------------------------------------------------------
# Deadlines
# --------------------------------------------------------------------------
CUE_RX = re.compile(
    r"last\s+date|closing\s+date|closes?\s+on|deadline|apply\s+(?:by|before)|on\s+or\s+before|due\s+date|\btill\b|\buntil\b",
    re.I,
)
DATE_RX = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s/.\-]+(\d{1,2}|[A-Za-z]{3,9})[\s/.\-,]+(\d{4})")
US_DATE_RX = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")


def _to_date(day: str, month: str, year: str) -> Optional[date]:
    try:
        if month.isdigit():
            return date(int(year), int(month), int(day))
        return datetime.strptime(f"{int(day)} {month[:3].title()} {year}", "%d %b %Y").date()
    except ValueError:
        return None


def find_deadline(text: str) -> Optional[str]:
    for cue in CUE_RX.finditer(text):
        window = text[cue.end(): cue.end() + 80]
        match = DATE_RX.search(window)
        if match:
            parsed = _to_date(*match.groups())
            if parsed:
                return parsed.isoformat()
        match = US_DATE_RX.search(window)
        if match:
            parsed = _to_date(match.group(2), match.group(1), match.group(3))
            if parsed:
                return parsed.isoformat()
    return None


def pdf_deadline(portal: Dict[str, object], url: str) -> Optional[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    resp = http_get(portal, url)
    if len(resp.content) > 5_000_000:
        return None
    reader = PdfReader(io.BytesIO(resp.content))
    text = " ".join((page.extract_text() or "") for page in reader.pages[:3])
    return find_deadline(" ".join(text.split()))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def load_state() -> Dict[str, dict]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(seen: Dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_iso(value: Optional[str], default: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value) if value else default
    except ValueError:
        return default


def prune(seen: Dict[str, dict], now: datetime) -> int:
    cutoff = now - timedelta(days=PRUNE_DAYS)
    stale = [k for k, v in seen.items() if k != "_meta" and _parse_iso(v.get("last_seen"), now) < cutoff]
    for key in stale:
        del seen[key]
    return len(stale)


# --------------------------------------------------------------------------
# Optional AI relevance layer (Claude)
# --------------------------------------------------------------------------
def ai_review(items: List[Dict[str, object]]) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not items:
        return
    batch = items[:AI_MAX_ITEMS]
    listing = "\n".join(f"{i}. [{it['portal']}] {it['title']}" for i, it in enumerate(batch))
    prompt = (
        f"Candidate profile: {CANDIDATE_PROFILE}\n\n"
        "For each job/notice title below, decide whether it is plausibly relevant for this candidate "
        "(fresher-level or student-eligible, in or near food technology, flavor, food safety, process, "
        "research, data/AI in food, or PG/PhD study). Reply with ONLY a JSON array, one object per item: "
        '{"id": <number>, "relevant": true|false, "summary": "<=15 word plain summary"}.\n\n'
        f"{listing}"
    )
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            "max_tokens": 2500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = "".join(block.get("text", "") for block in resp.json().get("content", []))
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError("no JSON array in model reply")
    for row in json.loads(match.group(0)):
        idx = row.get("id")
        if isinstance(idx, int) and 0 <= idx < len(batch):
            batch[idx]["relevant"] = bool(row.get("relevant", True))
            batch[idx]["summary"] = str(row.get("summary", ""))[:200]


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------
esc = html_lib.escape


def item_html(it: Dict[str, object]) -> str:
    bits = [f"<li><strong>{esc(str(it['portal']))}:</strong> "
            f"<a href='{esc(str(it['url']), quote=True)}'>{esc(str(it['title']))}</a>"]
    if it.get("score") is not None:
        bits.append(f" <small style='color:#888'>[{int(it['score']):+d}]</small>")
    if it.get("deadline"):
        bits.append(f" <small style='color:#b45f06'>closes {esc(str(it['deadline']))}</small>")
    if it.get("summary"):
        bits.append(f"<br><em style='color:#555'>{esc(str(it['summary']))}</em>")
    bits.append("</li>")
    return "".join(bits)


def item_text(it: Dict[str, object]) -> str:
    extra = []
    if it.get("score") is not None:
        extra.append(f"score {int(it['score']):+d}")
    if it.get("deadline"):
        extra.append(f"closes {it['deadline']}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    line = f"- {it['portal']}: {it['title']}{suffix}\n  {it['url']}"
    if it.get("summary"):
        line += f"\n  {it['summary']}"
    return line


def build_digest(
    now: datetime,
    baseline: bool,
    ok_count: int,
    total_portals: int,
    total_tracked: int,
    top: List[dict],
    by_category: Dict[str, List[dict]],
    closing: List[dict],
    weekly: Optional[List[Tuple[str, int]]],
    warnings: List[Tuple[str, str]],
) -> Tuple[str, str, str]:
    now_str = now.strftime("%d %b %Y, %I:%M %p IST")
    short = now.strftime("%d %b %Y")
    total_new = len(top) + sum(len(v) for v in by_category.values())

    if baseline:
        subject = f"[Job Monitor] Baseline Initialized ({total_tracked} links tracked)"
    else:
        noun = "Posting" if total_new == 1 else "Postings"
        subject = f"[Job Monitor] {total_new} New {noun} ({len(top)} top picks) - {short}"
        if closing:
            subject += f" | {len(closing)} closing soon"

    html_parts = [
        "<div style='font-family: Arial, sans-serif; color: #222; line-height: 1.5;'>",
        "<h2 style='margin-bottom: 4px;'>Global Food Tech &amp; Govt Job Monitor</h2>",
        f"<p style='color:#555; margin-top:0;'>{esc(now_str)} &middot; Checked {ok_count}/{total_portals} portals</p>",
    ]
    text = [f"Global Food Tech & Govt Job Monitor ({now_str})", f"Checked: {ok_count}/{total_portals} portals"]

    if baseline:
        html_parts.append(f"<p><strong>Baseline initialized</strong> with {total_tracked} existing links. Alerts start next run.</p>")
        text.append(f"\nBaseline initialized with {total_tracked} existing links. Alerts start next run.")
    elif total_new == 0 and not closing:
        html_parts.append("<p>No new postings today. Your monitor is active and healthy.</p>")
        text.append("\nNo new postings today. Monitor is active and healthy.")

    if closing:
        html_parts.append(f"<h3 style='color:#b45f06;'>Closing soon ({len(closing)})</h3><ul>")
        text.append(f"\n[CLOSING SOON] ({len(closing)})")
        for it in sorted(closing, key=lambda x: x["days_left"]):
            left = "today" if it["days_left"] == 0 else f"in {it['days_left']}d"
            html_parts.append(item_html(it).replace("</li>", f" <strong style='color:#b45f06'>({left})</strong></li>"))
            text.append(item_text(it) + f"\n  closes {left}")
        html_parts.append("</ul>")

    if top:
        html_parts.append(f"<h3 style='color:#d84315;'>Top picks ({len(top)})</h3><ul>")
        text.append(f"\n[TOP PICKS] ({len(top)})")
        for it in top[:MAX_PER_SECTION]:
            html_parts.append(item_html(it))
            text.append(item_text(it))
        html_parts.append("</ul>")

    ordered = [c for c in CATEGORY_ORDER if c in by_category] + [c for c in by_category if c not in CATEGORY_ORDER]
    for category in ordered:
        items = by_category[category]
        if not items:
            continue
        color = CATEGORY_COLORS.get(category, "#444")
        html_parts.append(f"<h3 style='color:{color};'>{esc(category)} ({len(items)} new)</h3><ul>")
        text.append(f"\n[{category.upper()}] ({len(items)} new)")
        for it in items[:MAX_PER_SECTION]:
            html_parts.append(item_html(it))
            text.append(item_text(it))
        html_parts.append("</ul>")

    if weekly is not None:
        html_parts.append("<h3>Weekly summary (new postings, last 7 days)</h3>")
        text.append("\n[WEEKLY SUMMARY] new postings, last 7 days")
        if weekly:
            html_parts.append("<ul>" + "".join(f"<li>{esc(n)}: {c}</li>" for n, c in weekly) + "</ul>")
            text.extend(f"- {n}: {c}" for n, c in weekly)
        else:
            html_parts.append("<p>None this week.</p>")
            text.append("- None this week.")

    if warnings:
        html_parts.append("<h3 style='color:#b45f06;'>Portal Warnings</h3><ul>")
        text.append("\n[PORTAL WARNINGS]")
        for name, err in warnings:
            html_parts.append(f"<li><strong>{esc(name)}:</strong> {esc(err[:200])}</li>")
            text.append(f"- {name}: {err[:200]}")
        html_parts.append("</ul>")

    html_parts.append("</div>")
    return subject, "\n".join(text), "".join(html_parts)


def send_gmail(sender: str, app_password: str, recipient: str, subject: str, text_body: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, app_password.replace(" ", "").strip())
        server.send_message(msg)
    print(f"Email sent successfully to {recipient}.")


def send_telegram(token: str, chat_id: str, items: List[dict]) -> None:
    lines = ["<b>Job Monitor: top picks</b>"]
    for it in items[:10]:
        lines.append(
            f"- <b>{esc(str(it['portal']))}</b>: "
            f"<a href=\"{esc(str(it['url']), quote=True)}\">{esc(str(it['title']))}</a> "
            f"(score {int(it['score']):+d})"
        )
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": "\n".join(lines)[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=20,
    )
    resp.raise_for_status()
    print("Telegram push sent.")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run() -> None:
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("RECIPIENT_EMAIL", "").strip() or gmail_address
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not gmail_address or not gmail_app_password:
        raise EnvironmentError("Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD environment variables.")

    now = datetime.now(IST)
    now_iso = now.isoformat(timespec="seconds")
    today = now.date()

    seen = load_state()
    migrated = bool(seen) and "_meta" not in seen  # state from the v1 script: IDs changed
    has_state = any(k != "_meta" for k in seen)
    baseline = migrated or not has_state
    meta = seen.setdefault("_meta", {})
    meta.setdefault("zero_streak", {})
    meta.setdefault("last_weekly", "")
    if migrated:
        for key, rec in seen.items():
            if key != "_meta":
                rec["first_seen"] = now_iso
                rec["last_seen"] = now_iso

    portal_by_name = {str(p["name"]): p for p in PORTALS}
    new_items: List[Dict[str, object]] = []
    warnings: List[Tuple[str, str]] = []
    notes: List[str] = []
    ok_count = 0
    renderer = Renderer()

    try:
        for portal in PORTALS:
            name = str(portal["name"])
            try:
                postings = fetch_postings(portal, renderer, notes)
                ok_count += 1
            except Exception as exc:
                warnings.append((name, f"fetch failed: {exc}"))
                continue

            streaks = meta["zero_streak"]
            streaks[name] = 0 if postings else streaks.get(name, 0) + 1

            for item in postings:
                record = seen.get(str(item["id"]))
                if record:
                    record["last_seen"] = now_iso
                    record["title"] = item["title"]
                    continue
                deadline = find_deadline(str(item["title"]))
                item["deadline"] = deadline
                seen[str(item["id"])] = {
                    "portal": item["portal"],
                    "category": item["category"],
                    "title": item["title"],
                    "url": item["url"],
                    "score": item["score"],
                    "deadline": deadline,
                    "relevant": True,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                }
                if not baseline:
                    new_items.append(item)
            time.sleep(1)
    finally:
        renderer.close()

    # Enrich new items: PDF deadlines, then AI relevance filter.
    if new_items:
        new_items.sort(key=lambda i: -int(i["score"]))
        pdf_budget = PDF_LOOKUPS_PER_RUN
        for item in new_items:
            if not item.get("deadline") and is_pdf_url(str(item["url"])) and pdf_budget > 0 and int(item["score"]) >= 1:
                pdf_budget -= 1
                try:
                    item["deadline"] = pdf_deadline(portal_by_name[str(item["portal"])], str(item["url"]))
                except Exception:
                    pass
        try:
            ai_review(new_items)
        except Exception as exc:
            warnings.append(("AI review", f"skipped ({type(exc).__name__})"))
        for item in new_items:
            record = seen[str(item["id"])]
            record["deadline"] = item.get("deadline")
            record["relevant"] = item.get("relevant", True)
            record["summary"] = item.get("summary", "")

    shown = [i for i in new_items if i.get("relevant", True)]
    top = sorted((i for i in shown if int(i["score"]) >= TOP_SCORE), key=lambda i: -int(i["score"]))
    rest = [i for i in shown if int(i["score"]) < TOP_SCORE]
    by_category: Dict[str, List[dict]] = {}
    for item in sorted(rest, key=lambda i: -int(i["score"])):
        by_category.setdefault(str(item["category"]), []).append(item)

    # Closing-soon reminders come from the whole state, not just new items.
    closing: List[dict] = []
    for key, rec in seen.items():
        if key == "_meta" or not rec.get("deadline") or rec.get("relevant", True) is False:
            continue
        try:
            days_left = (date.fromisoformat(rec["deadline"]) - today).days
        except ValueError:
            continue
        if 0 <= days_left <= DEADLINE_WARN_DAYS:
            closing.append({**rec, "days_left": days_left})

    # Weekly summary on Sundays (IST).
    weekly: Optional[List[Tuple[str, int]]] = None
    if not baseline and now.weekday() == 6 and meta.get("last_weekly") != today.isoformat():
        cutoff = now - timedelta(days=7)
        counts: Dict[str, int] = {}
        for key, rec in seen.items():
            if key != "_meta" and _parse_iso(rec.get("first_seen"), now) >= cutoff:
                counts[rec["portal"]] = counts.get(rec["portal"], 0) + 1
        weekly = sorted(counts.items(), key=lambda kv: -kv[1])
        meta["last_weekly"] = today.isoformat()

    # Health warnings.
    for name, streak in meta["zero_streak"].items():
        if streak >= ZERO_STREAK_WARN and name in portal_by_name:
            warnings.append((name, f"0 matching links for {streak} runs in a row - page may have changed or need JS rendering"))
    if INSECURE_PORTALS:
        warnings.append(("SSL", "certificate check skipped for: " + ", ".join(sorted(INSECURE_PORTALS))))
    for note in dict.fromkeys(notes):
        warnings.append(("Note", note))

    pruned = prune(seen, now)
    if pruned:
        print(f"Pruned {pruned} stale entries.")

    # Telegram push for high-score items.
    if tg_token and tg_chat and not baseline:
        push = [i for i in top if int(i["score"]) >= TG_MIN_SCORE]
        if push:
            try:
                send_telegram(tg_token, tg_chat, push)
            except Exception:
                warnings.append(("Telegram", "push failed (check bot token / chat id)"))

    total_tracked = sum(1 for k in seen if k != "_meta")
    subject, text_body, html_body = build_digest(
        now, baseline, ok_count, len(PORTALS), total_tracked, top, by_category, closing, weekly, warnings
    )
    print(text_body)

    # Send first, save after: if email fails, the same items are alerted on the next run.
    send_gmail(gmail_address, gmail_app_password, recipient, subject, text_body, html_body)
    save_state(seen)


if __name__ == "__main__":
    run()
