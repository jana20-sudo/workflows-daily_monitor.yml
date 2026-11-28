#!/usr/bin/env python3
"""Global Food Tech, Flavor, PhD & Govt Job Monitor (v2 Complete).

Features:
- Word-boundary regex matching, relevance scoring & "Top Picks" section
- Pre-Degree [ELIGIBLE NOW] vs [AFTER DEGREE] auto-tagging
- 36 portals: India Food Tech/FSO/ICAR/NIFTEM/CSIR, Govt/RRB, UN Food Safety,
  Flavor & Fragrance MNCs, Academia/PhD + RSS/Greenhouse/Lever JSON feeds
- Optional Playwright rendering for JS-heavy career portals
- Deadline extraction from titles & PDFs (pypdf) + "Closing Soon" reminders
- Sunday weekly summary + zero-link health checks + 180-day state pruning
- Gmail SMTP HTML digest + optional ntfy.sh push + optional Gemini AI review
"""
from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import os
import re
import smtplib
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
    "Undergraduate B.Tech Food Technology student (Data Science minor) targeting flavor/fragrance, "
    "encapsulation, food process optimization, food safety/regulatory roles, and MSc/PhD study abroad.",
)


def P(name: str, url: str, category: str, dedicated: bool = True, **extra) -> Dict[str, object]:
    return {"name": name, "url": url, "category": category, "dedicated": dedicated, **extra}


PORTALS: List[Dict[str, object]] = [
    # --- India Food Tech, State FSO, Research & Statutory Boards ---
    P("FSSAI", "https://www.fssai.gov.in/jobs-at-fssai", "India Food Tech"),
    P("Central Warehousing Corp (CWC)", "https://cwceportal.com/careers", "India Food Tech"),
    P("Food Corporation of India (FCI)", "https://fci.gov.in/", "India Food Tech", dedicated=False),
    P("Bureau of Indian Standards (BIS)", "https://www.bis.gov.in/", "India Food Tech", dedicated=False),
    P("TN MRB (Tamil Nadu FSO)", "https://mrb.tn.gov.in/content.php?menu_id=MTkw&dept_id=ODAw&lang=MQ==", "India Food Tech"),
    P("ICAR Vacancies", "https://icar.org.in/en/vacancies", "India Food Tech"),
    P("CSIR-CFTRI (Regular)", "https://cftri.res.in/recruitments/regular-positions", "India Food Tech"),
    P("CSIR-CFTRI (Project Staff)", "https://cftri.res.in/recruitments/project-positions", "India Food Tech"),
    P("CSIR-CLRI", "https://clri.org/", "India Food Tech", dedicated=False),
    P("NIFTEM Kundli", "https://www.niftem.ac.in/career/jobs?tab=openings", "India Food Tech"),
    P("NIFTEM Thanjavur", "https://niftem-t.ac.in/careers.php", "India Food Tech"),
    P("Tea Board of India", "https://www.teaboard.gov.in/", "India Food Tech", dedicated=False),
    P("Coffee Board of India", "https://coffeeboard.gov.in/News.aspx", "India Food Tech", dedicated=False),
    P("Spices Board India", "https://www.indianspices.com/", "India Food Tech", dedicated=False),
    P("APEDA", "https://apeda.gov.in/", "India Food Tech", dedicated=False),
    P("Anna University", "https://www.annauniv.edu/", "India Food Tech", dedicated=False),
    # --- General Indian Govt & Railways ---
    P("SSC (Notice Board)", "https://ssc.gov.in/home/notice-board", "Govt & RRB"),
    P("RRB Chennai", "https://www.rrbchennai.gov.in/", "Govt & RRB"),
    # --- International Food Safety & UN Agencies ---
    P("FAO (UN Food & Agriculture)", "https://www.fao.org/employment/en", "International"),
    P("FAO Internships (Freshers)", "https://www.fao.org/employment/young-talent-programme/internship-programme/en", "International"),
    P("EFSA (European Food Safety)", "https://careers.efsa.europa.eu/", "International", js=True),
    P("EFSA Young Professionals", "https://careers.efsa.europa.eu/youngprofessionals", "International", js=True),
    P("WFP (UN World Food Programme)", "https://www.wfp.org/careers", "International", js=True),
    # --- Flavor, Fragrance & Ingredient Leaders ---
    P("Symrise", "https://www.symrise.com/careers/", "Flavor & Ingredients", js=True),
    P("IFF", "https://jobs.iff.com/", "Flavor & Ingredients", js=True),
    P("Kemin", "https://www.kemin.com/na/en-us/about/careers", "Flavor & Ingredients", js=True),
    P("Givaudan", "https://www.givaudan.com/careers", "Flavor & Ingredients", js=True),
    P("dsm-firmenich", "https://www.dsm-firmenich.com/corporate/careers.html", "Flavor & Ingredients", js=True),
    P("Sensient", "https://sensient.com/careers/", "Flavor & Ingredients", js=True),
    P("Mane", "https://www.mane.com/careers", "Flavor & Ingredients", js=True),
    P("Takasago", "https://www.takasago.com/en/careers/", "Flavor & Ingredients", js=True),
    # --- Academia & PhD ---
    P("WUR Vacancies", "https://www.wur.nl/en/vacancies.htm", "Academia & PhD", js=True),
    P("WUR PhD Positions", "https://www.wur.nl/en/vacancies/phd-positions.htm", "Academia & PhD", js=True),
    P("EURAXESS (Food Science)", "https://euraxess.ec.europa.eu/jobs/search?keywords=food%20science", "Academia & PhD", js=True),
    P("FindAPhD (Food Science)", "https://www.findaphd.com/phds/food-science/", "Academia & PhD"),
]

# Optional Greenhouse / Lever JSON boards
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
# Keyword matching & relevance scoring
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
    r"\bsafety\b", r"\bflavou?r", r"\bfragrance", r"\bencapsulat", r"\bformulation", r"\bfso\b",
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

ELIGIBLE_NOW_RX = _rx(
    r"\bintern(?:ship)?s?\b", r"\bapprentice", r"\btrainee", r"\bchsl\b", r"10\+2",
    r"\bundergraduate\b", r"\bstudent\b", r"\bappearing\b", r"\bfinal year\b",
    r"\byoung professional", r"\bearly career", r"\bfreshers?\b", r"\bwalk-?in\b",
    r"\bproject assistant\b",
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


def tag_eligibility(portal_name: str, title: str) -> str:
    return "ELIGIBLE NOW" if ELIGIBLE_NOW_RX.search(f"{portal_name} {title}") else "AFTER DEGREE"


def is_pdf_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


def evaluate(portal: Dict[str, object], title: str, url: str) -> Optional[int]:
    if NOISE_RX.search(title):
        return None
    is_food = bool(FOOD_RX.search(title))
    is_job = bool(JOB_RX.search(title))
    dedicated = bool(portal.get("dedicated", False))
    if not (is_job or (dedicated and (is_food or is_pdf_url(url)))):
        return None
    return score_title(title)


# --------------------------------------------------------------------------
# HTTP + Playwright rendering
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

    def render(self, url: str, timeout_ms: int = 25000) -> str:
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
            page.wait_for_timeout(1200)
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
            "eligibility": tag_eligibility(str(portal["name"]), title),
            "title": title[:180],
            "url": url,
            "score": score,
            "deadline": find_deadline(title) or "",
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
# Deadlines (Titles + PDFs)
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
    try:
        resp = http_get(portal, url)
        if len(resp.content) > 5_000_000:
            return None
        reader = PdfReader(io.BytesIO(resp.content))
        text = " ".join((page.extract_text() or "") for page in reader.pages[:3])
        return find_deadline(" ".join(text.split()))
    except Exception:
        return None


# --------------------------------------------------------------------------
# State management
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
# Optional AI relevance layer (Gemini via Google AI Studio)
# --------------------------------------------------------------------------
def ai_review(items: List[Dict[str, object]]) -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or not items:
        return
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    batch = items[:AI_MAX_ITEMS]
    listing = "\n".join(f"{i}. [{it['portal']}] {it['title']}" for i, it in enumerate(batch))
    prompt = (
        f"Candidate profile: {CANDIDATE_PROFILE}\n\n"
        "For each job posting below, return ONLY a JSON array of objects with keys: "
        '{"index": int, "keep": bool, "note": "short 10-word fit & career-growth note"}.\n\n'
        f"{listing}"
    )
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        match = re.search(r"\[.*\]", raw, re.S)
        if not match:
            return
        parsed = json.loads(match.group(0))
        for entry in parsed:
            idx = entry.get("index")
            if isinstance(idx, int) and 0 <= idx < len(batch):
                if entry.get("note"):
                    batch[idx]["ai_note"] = str(entry["note"])[:120]
                if entry.get("keep") is False:
                    batch[idx]["score"] = int(batch[idx].get("score", 0)) - 2
    except Exception as exc:
        print(f"Gemini AI review skipped (non-fatal): {exc}")


# --------------------------------------------------------------------------
# Notifications (Gmail SMTP + optional ntfy.sh)
# --------------------------------------------------------------------------
def _render_item_html(item: Dict[str, object]) -> str:
    portal = html_lib.escape(str(item["portal"]))
    title = html_lib.escape(str(item["title"]))
    url = html_lib.escape(str(item["url"]), quote=True)
    elig = str(item.get("eligibility", "AFTER DEGREE"))
    badge_style = "background:#dcfce7;color:#166534;" if elig == "ELIGIBLE NOW" else "background:#f1f5f9;color:#475569;"
    badge = f"<span style='font-size:11px;font-weight:bold;padding:2px 6px;border-radius:4px;{badge_style}'>{elig}</span>"
    dl = f" <span style='color:#b45309;font-size:12px;'>(Deadline: {html_lib.escape(str(item['deadline']))})</span>" if item.get("deadline") else ""
    note = f"<br><span style='color:#475569;font-size:12px;'>{html_lib.escape(str(item['ai_note']))}</span>" if item.get("ai_note") else ""
    return f"<li style='margin-bottom:7px;'>{badge} <strong>{portal}:</strong> <a href='{url}'>{title}</a>{dl}{note}</li>"


def build_html_body(
    now_ist: str,
    ok_count: int,
    total_portals: int,
    first_run: bool,
    total_seen: int,
    new_items: List[Dict[str, object]],
    closing_soon: List[Dict[str, object]],
    weekly_picks: List[Dict[str, object]],
    failed_portals: List[Tuple[str, str]],
    notes: List[str],
) -> str:
    parts = [
        "<div style='font-family: Arial, sans-serif; color: #222; line-height: 1.5;'>",
        "<h2 style='margin-bottom: 4px;'>Global Food Tech, Flavor, PhD &amp; Govt Monitor (v2)</h2>",
        f"<p style='color: #555; margin-top: 0;'>{html_lib.escape(now_ist)} &middot; Checked {ok_count}/{total_portals} portals</p>",
    ]

    if first_run:
        parts.append(f"<p><strong>Baseline initialized</strong> with {total_seen} existing links across {total_portals} portals.</p>")
    else:
        if closing_soon:
            parts.append(f"<h3 style='color:#b91c1c;'>Closing Soon (&le; {DEADLINE_WARN_DAYS} days)</h3><ul>")
            for item in closing_soon[:15]:
                parts.append(_render_item_html(item))
            parts.append("</ul>")

        top_picks = [it for it in new_items if int(it.get("score", 0)) >= TOP_SCORE]
        if top_picks:
            parts.append(f"<h3 style='color:#d97706;'>Top Picks ({len(top_picks)} high-match)</h3><ul>")
            for item in top_picks[:15]:
                parts.append(_render_item_html(item))
            parts.append("</ul>")

        if not new_items:
            parts.append("<p>No new postings today. Your monitor is active and healthy.</p>")
        else:
            for cat in CATEGORY_ORDER:
                cat_items = [it for it in new_items if it["category"] == cat]
                if not cat_items:
                    continue
                color = CATEGORY_COLORS.get(cat, "#1e293b")
                parts.append(f"<h3 style='color:{color};'>{html_lib.escape(cat)} ({len(cat_items)} new)</h3><ul>")
                for item in cat_items[:MAX_PER_SECTION]:
                    parts.append(_render_item_html(item))
                parts.append("</ul>")

        if weekly_picks:
            parts.append(f"<h3 style='color:#334155;'>Sunday Weekly Highlights ({len(weekly_picks)} top items)</h3><ul>")
            for item in weekly_picks[:20]:
                parts.append(_render_item_html(item))
            parts.append("</ul>")

    if failed_portals or notes:
        parts.append("<h3 style='color:#b45f06;'>Health &amp; Portal Warnings</h3><ul>")
        for name, err in failed_portals:
            parts.append(f"<li><strong>{html_lib.escape(name)}:</strong> {html_lib.escape(err[:140])}</li>")
        for n in notes[:10]:
            parts.append(f"<li>{html_lib.escape(n)}</li>")
        parts.append("</ul>")

    parts.append("</div>")
    return "".join(parts)


def send_gmail(sender: str, app_password: str, recipient: str, subject: str, text_body: str, html_body: str) -> None:
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


def send_ntfy_push(topic: str, title: str, message: str, click_url: str = "", priority: str = "high") -> None:
    if not topic:
        return
    headers = {"Title": title[:120], "Priority": priority, "Tags": "briefcase" if priority == "high" else "white_check_mark"}
    if click_url:
        headers["Click"] = click_url
    try:
        resp = requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=15)
        resp.raise_for_status()
        print(f"ntfy.sh push sent to '{topic}'.")
    except Exception as exc:
        print(f"ntfy.sh warning (non-fatal): {exc}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run() -> None:
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient_email = os.environ.get("RECIPIENT_EMAIL", "").strip() or gmail_address
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()

    if not gmail_address or not gmail_app_password:
        raise EnvironmentError("Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD environment variables.")

    seen = load_state()
    meta = seen.setdefault("_meta", {"zero_streaks": {}})
    zero_streaks: Dict[str, int] = meta.setdefault("zero_streaks", {})
    first_run = len([k for k in seen if k != "_meta"]) == 0

    now_dt = datetime.now(IST)
    now_iso = now_dt.isoformat()
    now_ist = now_dt.strftime("%d %b %Y, %I:%M %p IST")
    short_date = now_dt.strftime("%d %b %Y")
    today = now_dt.date()

    new_items: List[Dict[str, object]] = []
    failed_portals: List[Tuple[str, str]] = []
    notes: List[str] = []
    ok_count = 0
    pdf_lookups = 0

    renderer = Renderer()
    try:
        for portal in PORTALS:
            pname = str(portal["name"])
            try:
                postings = fetch_postings(portal, renderer, notes)
                ok_count += 1
                if not postings:
                    zero_streaks[pname] = zero_streaks.get(pname, 0) + 1
                    if zero_streaks[pname] >= ZERO_STREAK_WARN:
                        notes.append(f"{pname}: returned 0 links for {zero_streaks[pname]} runs in a row.")
                else:
                    zero_streaks[pname] = 0

                for item in postings:
                    iid = str(item["id"])
                    if iid in seen:
                        seen[iid]["last_seen"] = now_iso
                        continue

                    if not first_run and not item["deadline"] and is_pdf_url(str(item["url"])) and pdf_lookups < PDF_LOOKUPS_PER_RUN:
                        item["deadline"] = pdf_deadline(portal, str(item["url"])) or ""
                        pdf_lookups += 1

                    seen[iid] = {
                        "portal": item["portal"],
                        "category": item["category"],
                        "eligibility": item["eligibility"],
                        "score": item["score"],
                        "deadline": item["deadline"],
                        "title": item["title"],
                        "url": item["url"],
                        "first_seen": now_iso,
                        "last_seen": now_iso,
                    }
                    if not first_run:
                        new_items.append(item)
            except Exception as exc:
                failed_portals.append((pname, str(exc)))
    finally:
        renderer.close()

    if INSECURE_PORTALS:
        notes.append(f"SSL fallback used for: {', '.join(sorted(INSECURE_PORTALS))}")

    new_items.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
    if new_items:
        ai_review(new_items)
        new_items.sort(key=lambda x: int(x.get("score", 0)), reverse=True)

    # Check deadlines closing soon (<= DEADLINE_WARN_DAYS)
    closing_soon: List[Dict[str, object]] = []
    for k, rec in seen.items():
        if k == "_meta" or not rec.get("deadline"):
            continue
        try:
            d = date.fromisoformat(str(rec["deadline"]))
            delta = (d - today).days
            if 0 <= delta <= DEADLINE_WARN_DAYS:
                closing_soon.append(rec)
        except ValueError:
            pass

    # Sunday weekly highlights (top-scoring postings from the last 7 days)
    weekly_picks: List[Dict[str, object]] = []
    if now_dt.weekday() == 6 and not first_run:
        week_ago = now_dt - timedelta(days=7)
        for k, rec in seen.items():
            if k == "_meta":
                continue
            if int(rec.get("score", 0)) >= TOP_SCORE and _parse_iso(rec.get("first_seen"), week_ago) >= week_ago:
                weekly_picks.append(rec)
        weekly_picks.sort(key=lambda x: int(x.get("score", 0)), reverse=True)

    pruned = prune(seen, now_dt)
    if pruned:
        notes.append(f"Pruned {pruned} old entries (> {PRUNE_DAYS} days).")

    save_state(seen)
    total_tracked = len([k for k in seen if k != "_meta"])

    lines = [f"Global Food Tech, Flavor, PhD & Govt Monitor ({now_ist})", f"Checked: {ok_count}/{len(PORTALS)} portals"]
    if first_run:
        subject = f"[Job Monitor v2] Baseline Initialized ({total_tracked} links)"
        lines.append(f"\nBaseline initialized with {total_tracked} links across {len(PORTALS)} portals.")
    elif not new_items and not closing_soon and not weekly_picks:
        subject = f"[Job Monitor v2] 0 New Postings Today ({short_date})"
        lines.append("\nNo new postings today. Monitor is active and healthy.")
    else:
        noun = "Posting" if len(new_items) == 1 else "Postings"
        subject = f"[Job Monitor v2] {len(new_items)} New {noun} ({short_date})"
        for item in new_items[:35]:
            dl = f" | Deadline: {item['deadline']}" if item.get("deadline") else ""
            lines.append(f"- [{item['eligibility']} | Score {item['score']}] {item['portal']}: {item['title']}{dl}\n  {item['url']}")

    if failed_portals:
        lines.append("\n[PORTAL WARNINGS]")
        for name, err in failed_portals:
            lines.append(f"- {name}: {err[:140]}")

    text_body = "\n".join(lines)
    html_body = build_html_body(
        now_ist, ok_count, len(PORTALS), first_run, total_tracked, new_items, closing_soon, weekly_picks, failed_portals, notes
    )

    print(text_body)
    send_gmail(gmail_address, gmail_app_password, recipient_email, subject, text_body, html_body)

    if ntfy_topic:
        if new_items:
            top_push = [f"[{i['eligibility']}] {i['portal']}: {str(i['title'])[:75]}" for i in new_items[:4]]
            send_ntfy_push(ntfy_topic, subject, "\n".join(top_push), click_url=str(new_items[0]["url"]), priority="high")
        else:
            status_msg = (
                f"Baseline initialized ({total_tracked} links tracked across {ok_count}/{len(PORTALS)} portals)."
                if first_run
                else f"Checked {ok_count}/{len(PORTALS)} portals. 0 new postings today—monitor is active."
            )
            send_ntfy_push(ntfy_topic, subject, status_msg, priority="low")


if __name__ == "__main__":
    run()
