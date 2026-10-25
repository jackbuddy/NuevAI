"""Shared settings for the NuevAI ingestion pipeline."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NUEVAI_DATA_DIR", ROOT / "data" / "pipeline"))
PAGES_DIR = DATA_DIR / "pages"          # one JSON per crawled page / document
LINKS_FILE = DATA_DIR / "links.json"    # classified important links
NEEDS_ACCESS_FILE = DATA_DIR / "needs_access.json"   # docs we could not read
CHUNKS_FILE = DATA_DIR / "chunks.json"
CALENDAR_FILE = DATA_DIR / "calendar_chunks.json"
PUBLIC_MARKER = DATA_DIR / "public_crawled.json"   # public site is crawled once, not refreshed
MANUAL_LIST_FILE = DATA_DIR / "DOWNLOAD_THESE.md"
REPORT_FILE = DATA_DIR / "report.json"

# Where the authenticated Nexus scraper lives and writes its pages.
NEXUS_SCRAPER_DIR = Path(os.getenv("NEXUS_SCRAPER_DIR", ROOT.parent / "nexus-scraper"))

USER_AGENT = "NuevAI-indexer/1.0 (school assistant; contact jacbudd@nuevaschool.org)"

# --- public site crawl -------------------------------------------------------
PUBLIC_HOST = "www.nuevaschool.org"
PUBLIC_SEEDS = [
    f"https://{PUBLIC_HOST}/",
    f"https://{PUBLIC_HOST}/site-map",
    f"https://{PUBLIC_HOST}/academics",
    f"https://{PUBLIC_HOST}/admissions",
    f"https://{PUBLIC_HOST}/school-life",
    f"https://{PUBLIC_HOST}/about",
    f"https://{PUBLIC_HOST}/contact-us",
]
MAX_PUBLIC_PAGES = int(os.getenv("MAX_PUBLIC_PAGES", "1500"))
# robots.txt asks for Crawl-delay 5; we never go faster than the site asks.
MIN_DELAY_SECONDS = 5.0

# --- chunking ----------------------------------------------------------------
CHUNK_TARGET_CHARS = 1100
CHUNK_MAX_CHARS = 1600
CHUNK_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 60

# --- Google files ------------------------------------------------------------
GOOGLE_HOSTS = {"docs.google.com", "drive.google.com", "sheets.google.com", "slides.google.com"}
MAX_GOOGLE_DEPTH = 2    # follow Google Doc -> linked Google Doc this many hops

# --- important-link classification ------------------------------------------
# (category, host regex, anchor/URL keyword regex). First match wins.
LINK_RULES = [
    ("booking", r"calendly\.com|youcanbook\.me|acuityscheduling\.com|squareup\.com/appointments|"
                r"signupgenius\.com|signup\.com|sign-up\.|bookings\.|setmore\.com|doodle\.com|"
                r"cal\.com|appointlet\.com|oncehub\.com|schedulicity\.com|ravenna-hub\.com",
     r"\b(book|booking|schedule|appointment|reserve|reservation|sign[- ]?up|tour|visit us|"
     r"conference|meet with|office hours|rsvp)\b"),
    ("form", r"forms\.gle|docs\.google\.com/forms|tfaforms\.com|formstack\.com|typeform\.com|"
             r"jotform\.com|airtable\.com/shr|surveymonkey\.com|fs/form-manager",
     r"\b(form|survey|register|registration|apply|application|request|submit|permission|waiver)\b"),
    ("payment", r"paypal\.com|stripe\.com|squareup\.com|nueva-store|bloomerang|givebutter|"
                r"classy\.org|onecause\.com|raisedonors|shopify|givecampus\.com",
     r"\b(pay|payment|donate|giving|gift|store|buy|purchase|tuition)\b"),
    ("calendar", r"calendar\.google\.com|\.ics$|myschoolapp\.com/.*calendar|athletics-calendar",
     r"\b(calendar|schedule of events|events)\b"),
    ("document", r"docs\.google\.com/(document|spreadsheets|presentation)|drive\.google\.com|"
                 r"fs/resource-manager|\.pdf($|\?)",
     r"\b(handbook|policy|catalog|guide|packet|calendar|syllabus|document|pdf)\b"),
    ("portal", r"myschoolapp\.com|blackbaud\.com|powerschool|schoology|canvas|naviance|raptor",
     r"\b(login|log in|portal|nexus|sign in)\b"),
    ("contact", r"^mailto:|^tel:", r"."),
]
LINK_RULES = [(c, re.compile(h, re.I), re.compile(k, re.I)) for c, h, k in LINK_RULES]

# Wording that marks an EXTERNAL link as actionable even on a host we have never
# seen (new booking tools appear all the time). Checked after the host rules.
INTENT_RULES = [
    ("booking", re.compile(r"\b(book|schedule|reserve|sign[- ]?up|register|rsvp)\b.{0,40}\b(tour|visit|event|appointment|"
                           r"conference|meeting|open house|info(rmation)? session|slot|time)\b|\bregister (for|online)\b", re.I)),
    ("payment", re.compile(r"\b(donate|make a gift|give now|pay (online|now|tuition)|buy tickets?)\b", re.I)),
    ("form", re.compile(r"\b(fill out|complete|submit)\b.{0,30}\b(form|application|survey)\b|\bapply (now|online)\b", re.I)),
]
SOCIAL_HOSTS = re.compile(r"facebook\.com|instagram\.com|twitter\.com|x\.com|linkedin\.com|youtube\.com|youtu\.be|"
                          r"vimeo\.com|finalsite\.com|tiktok\.com|pinterest\.com", re.I)

# Event pages from past years (e.g. 2019ilc.sched.com) are dead ends for families.
STALE_YEARS_BEFORE = 2024
STALE_URL = re.compile(r"(?<!\d)(20[01]\d|202[0-3])(?!\d)")

# Hand-verified key links. They always become link cards and win over crawled duplicates of the same service,
# so "how do I book a tour?" can never be answered with a bare homepage. Re-check these when the school changes tools.
PINNED_LINKS = [
    {"category": "booking", "label": "Book a campus tour or register for an admissions event (Ravenna)",
     "url": "https://www.ravenna-hub.com/schools/20642", "note": "Families register for tours, open houses and info sessions through Ravenna."},
    {"category": "booking", "label": "Visit & apply: admissions steps for every division",
     "url": "https://www.nuevaschool.org/admissions/visit-and-apply", "note": "Admissions visit and application steps."},
    {"category": "payment", "label": "Give to Nueva (donate online)",
     "url": "https://www.givecampus.com/campaigns/33909/donations/new", "note": "Make a gift to the school."},
    {"category": "payment", "label": "Nueva Store (spirit wear and merchandise)",
     "url": "https://www.nuevaschool.org/nueva-store", "note": "Buy school merchandise."},
    {"category": "portal", "label": "Nexus (MySchoolApp) sign in",
     "url": "https://nuevaschool.myschoolapp.com/", "note": "Grades, assignments and communications; requires a Nueva login."},
    {"category": "calendar", "label": "Official school calendar page",
     "url": "https://www.nuevaschool.org/about/news-events/calendar", "note": "The calendar page on the school website."},
]

# Categories worth a dedicated, directly retrievable "link card" chunk.
LINK_CARD_CATEGORIES = {"booking", "form", "payment", "calendar", "document", "portal"}


# --- live Google Sheets (public "anyone with the link" sheets re-read on every run) ----------------------------------
# kind "team_schedule": only the listed tabs are fetched (tab names are matched ignoring trailing spaces).
# kind "workbook": the whole workbook is downloaded as .xlsx and parsed by pipeline/workbooks.py.
LIVE_SHEETS = [
    {"kind": "team_schedule", "id": "1J5_IJlocnZMbREgyWXgDnqT5h6JQLEz-Ku0l3uxMkuA",
     "tabs": {"Varsity Schedule 26-27": "US Boys Basketball Varsity", "JV Schedule 26-27": "US Boys Basketball JV"}},
    {"kind": "workbook", "id": "1GpJrrLKDJEl5bW83cfa7FRGmgxr4yiNlU0C5JFXyMGY", "name": "Course Offerings by Block (live)"},
]

# Pages whose title matches are from a previous school year and are never indexed (they would contradict the current ones).
SUPERSEDED_TITLE = re.compile(r"25\s*[\u2013-]\s*26.{0,40}(clubs?|catalog)|(clubs?|catalog).{0,40}25\s*[\u2013-]\s*26", re.I)

# Pages that are navigation/login shells or people directories (contact cards): never worth embedding.
NOISE_TITLE = re.compile(r"^(login\b|student\s*:\s*directory$)", re.I)
# Lines that are UI chrome from copied web pages, not content.
BOILERPLATE_LINE = re.compile(r"^[-*\s]*(message|add to contacts|contact card|skip to (main )?content|share|print|back to top)\s*$|^[_\-=\u2500]{4,}$", re.I)
