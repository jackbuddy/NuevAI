"""Forwarded school emails -> knowledge base.

The owner forwards a school email (an info update, a schedule change) to a dedicated Gmail inbox. A GitHub Action polls that
inbox over IMAP every 30 minutes and, for every unread message:

  1. gatekeeping   the forwarder's From address must be on EMAIL_ALLOWED_SENDERS and Gmail's Authentication-Results header must show
                   dkim=pass or spf=pass for that address's domain; anything else is logged (rejected_*) and never indexed
  2. cleaning      text/plain preferred, else HTML -> text; the forwarded-message header block is parsed (original date and subject
                   become metadata) and removed; quoted replies, signatures, footers and tracking links are stripped
  3. privacy       messages that look like student-level data are NOT indexed (needs_review); non-staff email addresses and phone
                   numbers are redacted; instruction-like lines are scrubbed (guard.py)
  4. indexing      chunks `email-<content hash>-<n>` in document_chunks_v2, embedded like every other chunk, with the date it was sent.
                   Nothing expires and nothing is deleted: retrieval prefers the newest source and older emails stay as context.
  5. review log    one row per message in `email_ingest_log` (no bodies, no addresses); the message is then marked \\Seen

Attachments are ignored for now (the count is logged). Secrets and addresses come only from the environment and are never printed.
"""
import email
import hashlib
import imaplib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email import policy
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # guard.py / freshness.py / settings.py live at the repo root
import freshness   # noqa: E402
import guard       # noqa: E402
import settings    # noqa: E402

from . import chunker, config   # noqa: E402

LOG_TABLE = "email_ingest_log"
MAX_BODY_CHARS = 20_000
MAX_RAW_BYTES = 15_000_000
MAX_ERROR_ATTEMPTS = 3
STATUSES = ("indexed", "skipped_privacy", "rejected_sender", "rejected_auth", "duplicate", "error", "needs_review")
PROCESSED_LABEL = "nuevai-processed"

NOT_CONFIGURED = ("email ingest is not configured: set EMAIL_INGEST_USER, EMAIL_INGEST_APP_PASSWORD and EMAIL_ALLOWED_SENDERS "
                  "(GitHub Actions secrets or local environment variables). Nothing to do.")


# ============================================================================================ configuration
@dataclass
class Config:
    user: str
    password: str
    allowed: frozenset
    host: str = "imap.gmail.com"
    folder: str = "INBOX"

    def __repr__(self):                  # never let a stray print/log show the mailbox or the password
        return f"Config(host={self.host!r}, folder={self.folder!r}, allowed={len(self.allowed)} senders)"


def normalize_addr(value: str) -> str:
    """'Name <Teacher@Example.org>' -> 'teacher@example.org'. Gmail ignores dots and +tags in the local part, so they are dropped there."""
    addr = parseaddr(value or "")[1].strip().lower()
    if "@" not in addr:
        return ""
    local, domain = addr.rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def load_config(env=None) -> Config | None:
    """Config from the environment, or None when the feature is not set up (callers then exit cleanly)."""
    env = os.environ if env is None else env
    user = (env.get("EMAIL_INGEST_USER") or "").strip().strip("\"'")
    pw = re.sub(r"\s+", "", (env.get("EMAIL_INGEST_APP_PASSWORD") or "").strip().strip("\"'"))      # Google shows app passwords in groups of four
    allowed = frozenset(a for a in (normalize_addr(x) for x in re.split(r"[,;\s]+", env.get("EMAIL_ALLOWED_SENDERS") or "")) if a)
    if not user or not pw or not allowed:
        return None
    return Config(user=user, password=pw, allowed=allowed, host=(env.get("EMAIL_IMAP_HOST") or "imap.gmail.com").strip(),
                  folder=(env.get("EMAIL_INGEST_FOLDER") or "INBOX").strip())


# ============================================================================================ gatekeeping
def check_sender(msg: Message, cfg: Config) -> tuple[str, str, str, str]:
    """-> (verdict, reason, forwarder address (never logged), forwarder domain); verdict is ok | ignore | rejected_sender"""
    addr = normalize_addr(str(msg.get("From", "")))
    if not addr:
        return "rejected_sender", "no readable From address", "", ""
    dom = domain_of(addr)
    if addr == normalize_addr(cfg.user):
        return "ignore", "message from the ingest mailbox itself", addr, dom
    if addr not in cfg.allowed:
        return "rejected_sender", "sender is not on the allowlist", addr, dom
    return "ok", "", addr, dom


def _domains_in(segment: str) -> set[str]:
    """Domains an Authentication-Results method segment says it verified (header.d / header.i / smtp.mailfrom / 'domain of x@y')."""
    out = set()
    for key in ("header.d", "header.i", "smtp.mailfrom", "smtp.helo", "header.from"):
        for v in re.findall(rf"{re.escape(key)}=([^\s;()]+)", segment, re.I):
            out.add(v.strip("<>\"'").rsplit("@", 1)[-1].lower())
    m = re.search(r"domain of ([^\s)]+)", segment, re.I)
    if m:
        out.add(m.group(1).strip("<>\"'").rsplit("@", 1)[-1].lower())
    return out


def check_auth(msg: Message, domain: str) -> tuple[bool, str]:
    """Gmail's own Authentication-Results header (the top-most one: Gmail prepends it, so a forged copy inside the message sits below)
    must show dkim=pass or spf=pass for the forwarder's domain (an exact match or a subdomain of it)."""
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False, "no Authentication-Results header"
    top = re.sub(r"\s+", " ", str(headers[0]))
    serv = top.split(";", 1)[0].strip().lower()
    if not (serv == "google.com" or serv.endswith(".google.com")):
        return False, "top Authentication-Results header is not from Gmail"
    for seg in top.split(";")[1:]:
        m = re.match(r"\s*(dkim|spf)\s*=\s*(\w+)", seg, re.I)
        if m and m.group(2).lower() == "pass":
            if any(d == domain or d.endswith("." + domain) for d in _domains_in(seg)):
                return True, ""
    return False, "neither dkim nor spf passed for the sender's domain"


# ============================================================================================ cleaning
FORWARD_MARK = re.compile(r"^\s*(?:[-_=*]{2,}\s*(?:Forwarded message|Original Message|Forwarded by [^-]*?)\s*[-_=*]{2,}|Begin forwarded message:?)\s*$", re.I)
HEADER_LINE = re.compile(r"^\s*(From|Date|Sent|Subject|To|Cc|Bcc|Reply-To)\s*:\s*(.*)$", re.I)
REPLY_INTRO = re.compile(r"^\s*(?:On\b.{5,250}\bwrote:|Le\b.{5,250}\bécrit\s*:|El\b.{5,250}\bescribió:)\s*$", re.I)
SIG_DELIM = re.compile(r"^--\s*$|^_{8,}\s*$")
DEVICE_SIG = re.compile(r"^\s*(Sent from my \w[\w ]*|Sent from (?:Mail|Outlook|Yahoo Mail) for \w+|Get Outlook for \w+|Sent via \w[\w ]*)\.?\s*$", re.I)
CLOSER = re.compile(r"^\s*(best(?: regards| wishes)?|kind regards|warm(?:est)? regards|warmly|regards|sincerely|cheers|thanks(?: so much| again)?|thank you|"
                    r"with gratitude|gratefully|yours truly|respectfully)\s*[,!.]?\s*$", re.I)
CONFIDENTIAL = re.compile(r"^\s*(?:this (?:e-?mail|message)(?: and any attachments?)? (?:is|are|may be|contains?).{0,40}(?:confidential|privileged)|confidentiality notice|disclaimer\s*:)", re.I)
FOOTER_LINE = re.compile(
    r"unsubscribe|view (?:this )?(?:e-?mail|message|newsletter) in (?:your )?browser|having trouble viewing|update (?:your )?(?:e-?mail )?(?:preferences|profile)|"
    r"you (?:are )?(?:receiv(?:ed|ing)|got) this (?:e-?mail|message)|manage (?:your )?(?:subscription|preferences)|this (?:e-?mail|message) was sent to|"
    r"privacy policy|all rights reserved|©|forward (?:this )?to a friend|if you (?:no longer|do not|don'?t) wish to receive|to stop receiving|"
    r"you(?:'re| are) (?:subscribed|on our mailing list)|add .{1,40} to your address book|sent (?:with|using|via) (?:mailchimp|constant contact|sendgrid)|powered by|"
    r"mailing address\s*:|our mailing address", re.I)
TRACKING_HOST = re.compile(
    r"(?:^|\.)(?:list-manage\d*\.com|mailchi\.mp|sendgrid\.net|mandrillapp\.com|mailgun\.org|constantcontact\.com|rs6\.net|r20\.rs6\.net|"
    r"campaign-archive\d*\.com|ablink\.[\w.-]+|hubspotlinks\.com|hs-sites\.com|links?\.[\w.-]+|click\.[\w.-]+|clicks\.[\w.-]+|track\.[\w.-]+|"
    r"trk\.[\w.-]+|tracking\.[\w.-]+|e\.[\w.-]+|email\.[\w.-]+|go\.pardot\.com|pardot\.com|sparkpostmail\d*\.com|sendibm\d*\.com|"
    r"bytebroadcast\.com|blackbaudhosting\.com/open|myschoolapp\.com/track)$", re.I)
TRACKING_PATH = re.compile(r"/(?:track|tracking|click|open|wf/click|wf/open|pixel|beacon|unsubscribe|optout|opt-out|preferences)(?:/|\?|$)|[?&](?:unsubscribe|optout)=", re.I)
TRACKING_PARAM = re.compile(r"^(?:utm_\w+|fbclid|gclid|mc_(?:cid|eid)|mkt_tok|_hsenc|_hsmi|hsctatracking|igshid|ref_?src|s_cid|vero_\w+|trk\w*)$", re.I)
URL = re.compile(r"https?://[^\s<>()\[\]\"']+[^\s<>()\[\]\"'.,;:!?]")


def clean_url(url: str) -> str | None:
    """A real link without tracking parameters, or None when it is a tracking/unsubscribe redirect."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    host = u.netloc.lower().split("@")[-1].split(":")[0]
    qs = dict(parse_qsl(u.query, keep_blank_values=True))
    if host.endswith("safelinks.protection.outlook.com") and qs.get("url"):           # Outlook link scanner wraps the real link
        return clean_url(unquote(qs["url"]))
    if host in ("www.google.com", "google.com") and u.path == "/url" and (qs.get("q") or qs.get("url")):
        return clean_url(qs.get("q") or qs["url"])
    if TRACKING_HOST.search(host) or TRACKING_PATH.search(u.path + ("?" + u.query if u.query else "")) or len(url) > 400:
        return None
    kept = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING_PARAM.match(k)]
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(kept), ""))


def clean_links(text: str) -> str:
    """Bare URLs and markdown links: tracking ones lose the URL (a label stays), real ones lose their tracking parameters."""
    def md(m):
        label, url = m.group(1).strip(), clean_url(m.group(2))
        if not url:
            return label
        return url if (not label or label.rstrip("/") == m.group(2).rstrip("/") or URL.fullmatch(label)) else f"[{label}]({url})"

    text = re.sub(r"\[([^\]\n]{0,200})\]\((https?://[^\s)]+)\)", md, text)
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    parts = re.split(r"(\[[^\]\n]*\]\([^)\s]+\))", text)          # leave finished markdown links alone
    return "".join(p if p.startswith("[") and "](" in p else URL.sub(lambda m: clean_url(m.group(0)) or "", p) for p in parts)


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "head", "title", "meta", "noscript", "img", "svg"]):
        t.decompose()
    for a in soup.find_all("a"):
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = (a.get("href") or "").strip()
        url = clean_url(href) if href else None
        if href.lower().startswith(("mailto:", "tel:")) or not url:
            a.replace_with(label)
        elif not label or label.rstrip("/") == url.rstrip("/"):
            a.replace_with(url)
        else:
            a.replace_with(f"[{label}]({url})")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        h.insert_before("\n\n## ")
        h.insert_after("\n\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for c in soup.find_all(["td", "th"]):
        c.insert_after(" | ")
    for t in soup.find_all(["p", "div", "tr", "table", "blockquote", "ul", "ol", "section"]):
        t.insert_after("\n")
    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]*\|[ \t]*(?=\n|$)", "", text)                 # trailing cell separators
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def parse_date(value: str) -> date | None:
    """A date out of an RFC 2822 header or a mail client's own wording ('Tue, Oct 27, 2026 at 3:14 PM', 'Sent: Tuesday, October 27, 2026')."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError):
        pass
    found = freshness.full_dates_in(value)
    if found:
        return found[0]
    m = re.search(r"(?<!\d)(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})", value, re.I)
    if m:
        try:
            return date(int(m.group(3)), freshness.MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _peel_forward(lines: list[str]) -> tuple[dict, list[str]] | None:
    """If the text holds a forwarded-message block, return (its headers, the lines after it). Looks for a '---------- Forwarded message ---------'
    marker (then its From:/Date:/Subject: lines) or, without a marker, a From: line followed by Subject:/Date: lines."""
    start = None
    for i, l in enumerate(lines):
        if FORWARD_MARK.match(l):
            start = i + 1
            break
    if start is None:
        for i, l in enumerate(lines):
            m = HEADER_LINE.match(l.lstrip("> ").rstrip())
            if m and m.group(1).lower() == "from":
                nxt = [HEADER_LINE.match(x.lstrip("> ").rstrip()) for x in lines[i + 1:i + 6]]
                if sum(1 for n in nxt if n and n.group(1).lower() in ("date", "sent", "subject")) >= 2:
                    start = i
                    break
    if start is None:
        return None
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    headers, last = {}, None
    while i < len(lines) and len(headers) < 12:
        raw = lines[i].lstrip("> ").rstrip()
        m = HEADER_LINE.match(raw)
        if m:
            last = m.group(1).lower()
            headers.setdefault(last, m.group(2).strip())
        elif raw.strip() and last in ("to", "cc", "bcc", "reply-to", "subject") and (lines[i][:1] in " \t" or "@" in raw or raw.endswith(",")):
            headers[last] += " " + raw.strip()       # a wrapped To:/Cc: line
        else:
            break
        i += 1
    if not headers:
        return None
    return headers, lines[i:]


def _unquote_block(lines: list[str]) -> list[str]:
    """Apple Mail and others quote the whole forwarded message with '> ': remove one level when EVERY non-empty line has it."""
    body = [l for l in lines if l.strip()]
    if body and all(l.lstrip().startswith(">") for l in body):
        return [re.sub(r"^\s*>\ ?", "", l) for l in lines]
    return lines


def _cut_reply_chain(lines: list[str]) -> list[str]:
    out = []
    for i, l in enumerate(lines):
        joined = l + " " + lines[i + 1] if i + 1 < len(lines) and l.lstrip().lower().startswith(("on ", "le ", "el ")) and not l.rstrip().endswith(":") else l
        if REPLY_INTRO.match(l) or REPLY_INTRO.match(joined.strip()) and joined is not l:
            break
        if FORWARD_MARK.match(l) and any(x.strip() for x in out):         # an older message further down the chain
            break
        h = HEADER_LINE.match(l)
        if h and h.group(1).lower() == "from" and any(x.strip() for x in out) and \
                any((m := HEADER_LINE.match(x)) and m.group(1).lower() in ("sent", "date") for x in lines[i + 1:i + 4]):
            break
        if l.lstrip().startswith(">"):
            continue
        out.append(l)
    return out


def _cut_signature(lines: list[str]) -> list[str]:
    for i, l in enumerate(lines):
        if SIG_DELIM.match(l.rstrip("\n")) or DEVICE_SIG.match(l) or CONFIDENTIAL.match(l):
            lines = lines[:i]
            break
    nonblank = [i for i, l in enumerate(lines) if l.strip()]
    for i in reversed(nonblank):          # "Best,\nName\nTitle\n..." in the last few lines
        if CLOSER.match(lines[i]):
            if sum(1 for j in nonblank if j > i) <= 8:
                lines = lines[:i]
            break
        if len(nonblank) - nonblank.index(i) > 10:
            break
    return lines


@dataclass
class Cleaned:
    text: str
    subject: str
    sent: date | None
    from_name: str = ""
    from_domain: str = ""
    forwarded: bool = False
    scrubbed: bool = False


def strip_subject(subject: str) -> str:
    s = re.sub(r"\s+", " ", subject or "").strip()
    while True:
        n = re.sub(r"^\s*(?:re|fwd?|fw|rv|tr)\s*:\s*", "", s, flags=re.I)
        if n == s:
            return s
        s = n


def clean_body(raw_text: str, outer_subject: str = "", outer_date: date | None = None, outer_headers: dict | None = None) -> Cleaned:
    """Plain text of a message (forwarded or not) -> the facts to index plus the original subject/date/sender."""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[​-‏‪-‮⁠-⁤﻿­]", "", text).replace("\xa0", " ")
    lines = text.split("\n")
    headers, forwarded = dict(outer_headers or {}), False
    for _ in range(3):                                   # a forward of a forward: the innermost header block is the original
        peeled = _peel_forward(lines)
        if not peeled:
            break
        headers, lines = peeled
        lines = _unquote_block(lines)
        forwarded = True
    lines = _cut_reply_chain(lines)
    lines = _cut_signature(lines)
    text = "\n".join(l.rstrip() for l in lines)
    text = clean_links(text)
    kept = [l for l in text.split("\n") if not FOOTER_LINE.search(l)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    sender_name, sender_addr = parseaddr(headers.get("from", "")) if forwarded else ("", "")
    sender_addr = sender_addr.lower()
    staff = bool(sender_addr) and guard.is_staff_email(sender_addr)
    sent = parse_date(headers.get("date") or headers.get("sent") or "") if forwarded else None
    return Cleaned(text=text, subject=strip_subject(headers.get("subject") or outer_subject) or strip_subject(outer_subject),
                   sent=sent or outer_date, from_name=re.sub(r"\s+", " ", sender_name).strip()[:80] if staff else "",
                   from_domain=domain_of(sender_addr), forwarded=forwarded)


# ============================================================================================ message -> text
def _decode(part: Message) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def _leaves(msg: Message):
    """Leaf parts of a message; an attached message (message/rfc822) is yielded as one part, not descended into."""
    if msg.get_content_type() == "message/rfc822" or not msg.is_multipart():
        yield msg
        return
    for p in msg.get_payload():
        yield from _leaves(p)


def extract(msg: Message) -> tuple[str, int, Message | None]:
    """-> (text, number of attachments skipped, an attached forwarded message if the owner used 'forward as attachment').
    text/plain wins over text/html; attachments (files, images) are counted and ignored."""
    plain, html, skipped, inner = [], [], 0, None
    for part in _leaves(msg):
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        if ctype == "message/rfc822":
            payload = part.get_payload()
            inner = inner or (payload[0] if isinstance(payload, list) and payload else None)
            continue
        if disp == "attachment" or part.get_filename():
            skipped += 1
        elif ctype == "text/plain":
            plain.append(_decode(part))
        elif ctype == "text/html":
            html.append(_decode(part))
        elif ctype.startswith(("image/", "application/", "audio/", "video/")):
            skipped += 1
    if inner is not None:                        # 'forward as attachment': the attached original is the content, the cover note is the forwarder's
        text, n, _ = extract(inner)
        return text, skipped + n, inner
    if any(p.strip() for p in plain):
        return "\n\n".join(plain), skipped, inner
    return "\n\n".join(html_to_text(h) for h in html), skipped, inner


# ============================================================================================ analysis (pure: no network)
@dataclass
class Result:
    status: str
    reason: str = ""
    subject: str = ""
    sender_domain: str = ""
    message_id_hash: str = ""
    content_key: str = ""
    chunks: list = field(default_factory=list)
    attachments_skipped: int = 0
    received_at: str | None = None
    source_date: str = ""

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def hash_id(value: str, n: int = 16) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", value or "").strip("<>").lower().encode()).hexdigest()[:n]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def build_chunks(title: str, text: str, sent: date, content_key: str, message_id_hash: str, sender_domain: str,
                 from_name: str = "", from_domain: str = "", attachments: int = 0, now: datetime | None = None) -> list[dict]:
    """Chunk with the pipeline's own conventions (heading trail, packed to CHUNK_TARGET_CHARS) and the freshness metadata."""
    label = f"school email, {freshness.day_label(sent.isoformat())}"
    pieces = []
    for trail, body in chunker.sections(chunker.promote_record_titles(text), title):
        pieces += [(trail, p) for p in chunker.pack(body)]
    if not pieces and text.strip():
        pieces = [(title, text.strip())]
    split = []
    for trail, piece in pieces:                       # a very long unpunctuated line is cut on word boundaries
        while len(piece) > config.CHUNK_MAX_CHARS:
            cut = piece.rfind(" ", 0, config.CHUNK_TARGET_CHARS)
            cut = cut if cut > 200 else config.CHUNK_TARGET_CHARS
            split.append((trail, piece[:cut].strip()))
            piece = piece[cut:].strip()
        split.append((trail, piece))
    pieces = [x for x in split if x[1]]
    fresh = freshness.email_meta(sent.isoformat(), now)
    out = []
    for n, (trail, piece) in enumerate(pieces):
        head = f"Title: {title}\n" + (f"Section: {trail}\n" if trail != title else "") + f"Source: {label}\n\n"
        content = head + piece
        meta = {"source": "email", "source_type": "email", "source_url": f"email://{content_key}", "title": title,
                "sender_domain": sender_domain, "message_id_hash": message_id_hash, "content_key": content_key,
                "content_hash": hashlib.sha1(content.encode()).hexdigest(), "attachments_skipped": attachments, **fresh}
        if from_name:
            meta["original_from_name"] = from_name          # staff display name only (never an address)
        if from_domain:
            meta["original_from_domain"] = from_domain
        out.append({"chunk_id": f"{freshness.EMAIL_ID_PREFIX}{content_key}-{n:03d}", "content": content,
                    "source_file": f"email://{content_key}", "metadata": meta})
    return out


def analyze(raw: bytes, cfg: Config, now: datetime | None = None) -> Result:
    """One raw message -> a Result (status plus, when indexable, its chunks). Does no I/O, so every rule is testable offline."""
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        return Result("error", "could not parse the message")
    mid = hash_id(str(msg.get("Message-ID") or "")) if msg.get("Message-ID") else hash_id(raw[:4000].decode("latin-1"))
    outer_date = parse_date(str(msg.get("Date") or ""))
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).isoformat() if msg.get("Date") else None
    except (TypeError, ValueError):
        received = None
    outer_subject = strip_subject(str(msg.get("Subject") or ""))
    verdict, why, addr, dom = check_sender(msg, cfg)
    base = dict(sender_domain=dom, message_id_hash=mid, received_at=received)
    if verdict == "ignore":
        return Result("ignore", why, **base)
    if verdict != "ok":
        return Result("rejected_sender", why, **base)
    ok, why = check_auth(msg, dom)
    if not ok:
        return Result("rejected_auth", why, subject=outer_subject[:120], **base)
    try:
        text, skipped, inner = extract(msg)
    except Exception:
        return Result("error", "could not read the message body", subject=outer_subject[:120], **base)
    inner_headers = {k.lower(): str(inner.get(k) or "") for k in ("From", "Date", "Subject")} if inner is not None else None
    c = clean_body(text, outer_subject, outer_date, inner_headers)
    if inner is not None and not c.forwarded:
        c.forwarded = True
        c.sent = parse_date(inner_headers["date"]) or c.sent
        c.subject = strip_subject(inner_headers["subject"]) or c.subject
    subject = (c.subject or outer_subject or "School email")[:200]
    base.update(subject=subject[:120], attachments_skipped=skipped)
    if len(c.text.strip()) < 15:
        return Result("needs_review", "no readable text after cleaning (attachment-only or empty message?)", **base)
    reason = guard.student_data_reason(subject + "\n" + c.text)
    if reason:
        return Result("needs_review", f"looks like student-level data: {reason}", **base)
    text, redacted = guard.redact_contacts(c.text)
    title, _ = guard.redact_contacts(subject)
    scrubbed_text = guard.scrub_context(text)
    scrubbed = scrubbed_text != text
    text = scrubbed_text.strip()[:MAX_BODY_CHARS]
    if len(re.sub(r"\[(?:email|phone) removed\]", "", text).strip()) < 15:
        return Result("skipped_privacy", "nothing left after removing private contact details", **base)
    sent = c.sent or outer_date or (now or datetime.now()).date()
    if sent > (now or datetime.now()).date():
        sent = (now or datetime.now()).date()
    key = hash_id(_norm(title) + "\n" + _norm(text))
    chunks = build_chunks(title, text, sent, key, mid, dom, c.from_name, c.from_domain, skipped, now)
    notes = [n for n in (f"{redacted} contact detail(s) redacted" if redacted else "", "instruction-like lines scrubbed" if scrubbed else "",
                         "forwarded block parsed" if c.forwarded else "no forward block, used the message itself") if n]
    return Result("indexed", "; ".join(notes), subject=title[:120], content_key=key, chunks=chunks, attachments_skipped=skipped,
                  sender_domain=dom, message_id_hash=mid, received_at=received, source_date=sent.isoformat())


# ============================================================================================ storage
class SupabaseStore:
    """document_chunks_v2 (chunks) and email_ingest_log (review log). The log degrades to stdout when its table does not exist yet."""

    def __init__(self, sb=None, embed_upsert=None, out=print):
        if sb is None:
            from .index import client
            sb = client()
        if embed_upsert is None:
            from .index import _embed_and_upsert as embed_upsert
        self.sb, self._embed_upsert, self.out = sb, embed_upsert, out
        self.log_ok = True

    # -- chunks
    def _like(self, prefix: str) -> list[dict]:
        r = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id,h:metadata->>content_hash").like("chunk_id", prefix + "%").execute()
        return r.data or []

    def existing_for_key(self, content_key: str) -> dict:
        return {x["chunk_id"]: x.get("h") for x in self._like(f"{freshness.EMAIL_ID_PREFIX}{content_key}-")}

    def ids_for_message(self, message_id_hash: str) -> list[str]:
        r = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id").eq("metadata->>message_id_hash", message_id_hash) \
            .like("chunk_id", freshness.EMAIL_ID_PREFIX + "%").execute()
        return [x["chunk_id"] for x in (r.data or [])]

    def upsert(self, chunks: list[dict]) -> None:
        for i in range(0, len(chunks), 4):
            self._embed_upsert(self.sb, chunks[i:i + 4])

    def delete_replaced(self, ids: list[str]) -> int:
        """Remove chunks of THIS email that a re-processing replaced (never anything but email-* rows)."""
        ids = [i for i in ids if i.startswith(freshness.EMAIL_ID_PREFIX)]
        for i in range(0, len(ids), 50):
            self.sb.table(settings.CHUNKS_TABLE).delete().in_("chunk_id", ids[i:i + 50]).execute()
        return len(ids)

    # -- log
    def prior(self, message_id_hash: str) -> list[dict]:
        if not self.log_ok:
            return []
        try:
            return self.sb.table(LOG_TABLE).select("status").eq("message_id_hash", message_id_hash).execute().data or []
        except Exception:
            self._log_missing()
            return []

    def _log_missing(self):
        if self.log_ok:
            self.log_ok = False
            self.out(f"note: table {LOG_TABLE} is not available (see supabase/email_ingest_log.sql); logging to stdout only")

    def log(self, r: Result) -> None:
        row = {"received_at": r.received_at, "status": r.status, "subject": (r.subject or "")[:120], "sender_domain": r.sender_domain,
               "message_id_hash": r.message_id_hash, "chunk_count": r.chunk_count, "reason": (r.reason or "")[:300],
               "attachments_skipped": r.attachments_skipped}
        if self.log_ok:
            try:
                self.sb.table(LOG_TABLE).insert(row).execute()
                return
            except Exception:
                self._log_missing()


def index_result(r: Result, store) -> Result:
    """Dedupe, replace and write one analyzed message. Identical content (a re-forward) is a duplicate; changed content for the same
    message replaces its old chunks. Returns the Result with its final status."""
    have = store.existing_for_key(r.content_key)
    new = {c["chunk_id"]: c for c in r.chunks}
    if have and all(have.get(i) == c["metadata"]["content_hash"] for i, c in new.items()) and len(have) == len(new):
        r.status, r.reason, r.chunks = "duplicate", "same content is already indexed", []
        return r
    changed = [c for i, c in new.items() if have.get(i) != c["metadata"]["content_hash"]]
    store.upsert(changed)
    stale = [i for i in have if i not in new] + [i for i in store.ids_for_message(r.message_id_hash) if i not in new]
    removed = store.delete_replaced(list(dict.fromkeys(stale)))
    if removed:
        r.reason = (r.reason + "; " if r.reason else "") + f"replaced {removed} older chunk(s) of this message"
    return r


# ============================================================================================ run loop
def _fetch_uids(M, limit: int) -> list[bytes]:
    typ, data = M.uid("SEARCH", None, "UNSEEN")
    if typ != "OK":
        raise RuntimeError("IMAP search failed")
    return (data[0] or b"").split()[:limit]


def mark_processed(M, uid: bytes) -> None:
    M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    try:
        M.uid("STORE", uid, "+X-GM-LABELS", f"({PROCESSED_LABEL})")       # Gmail only; harmless elsewhere
    except Exception:
        pass


def handle(raw: bytes, cfg: Config, store, dry_run: bool, now: datetime | None = None) -> Result:
    if len(raw) > MAX_RAW_BYTES:
        return Result("error", "message larger than 15 MB")
    r = analyze(raw, cfg, now)
    if r.status != "indexed" or dry_run or store is None:
        if store is not None and not dry_run and r.status in ("rejected_sender", "rejected_auth", "needs_review", "skipped_privacy"):
            prior = [p["status"] for p in store.prior(r.message_id_hash)]
            if prior:
                r.status, r.reason = "duplicate", "already handled in an earlier run"
        return r
    try:
        return index_result(r, store)          # identical content is a duplicate, changed content replaces this message's chunks
    except Exception as e:
        r.status, r.chunks, r.reason = "error", [], f"{type(e).__name__} while indexing"
        return r


def run(cfg: Config, store, dry_run: bool = False, limit: int = 50, out=print, show_subjects: bool = False, imap=None) -> int:
    """Poll the mailbox once. Returns a process exit code: 0 (also when there is no new mail), 1 on a real error."""
    try:
        M = imap or imaplib.IMAP4_SSL(cfg.host, 993, timeout=60)
        M.login(cfg.user, cfg.password)
    except Exception as e:
        out(f"IMAP connection/login failed ({type(e).__name__}). Check EMAIL_INGEST_USER and EMAIL_INGEST_APP_PASSWORD (a Gmail app password, IMAP enabled).")
        return 1
    errors, counts = 0, {}
    try:
        typ, _ = M.select(cfg.folder)
        if typ != "OK":
            out(f"cannot open mailbox folder {cfg.folder!r}")
            return 1
        uids = _fetch_uids(M, limit)
        if not uids:
            out("no new mail")
            return 0
        out(f"{len(uids)} unread message(s){' (dry run: nothing is written or marked read)' if dry_run else ''}")
        for uid in uids:
            r = None
            try:
                typ, data = M.uid("FETCH", uid, "(BODY.PEEK[])")          # PEEK: fetching must not mark the message read
                raw = next((d[1] for d in data if isinstance(d, tuple)), b"")
                r = handle(raw, cfg, store, dry_run)
            except Exception as e:
                r = Result("error", f"{type(e).__name__} while processing")
            counts[r.status] = counts.get(r.status, 0) + 1
            line = f"  {r.status}"
            if show_subjects and r.subject:
                line += f": {r.subject[:80]}"
            line += f" [{r.message_id_hash[:8]}]" + (f" chunks={r.chunk_count}" if r.chunk_count else "") + \
                    (f" attachments skipped={r.attachments_skipped}" if r.attachments_skipped else "") + (f" ({r.reason})" if r.reason else "")
            out(line)
            if dry_run:
                continue
            if r.status == "error":
                prior_errors = sum(1 for p in store.prior(r.message_id_hash) if p["status"] == "error") if r.message_id_hash else MAX_ERROR_ATTEMPTS
                store.log(r)
                if prior_errors + 1 >= MAX_ERROR_ATTEMPTS:         # give up after a few runs so one bad message does not fail every 30 minutes
                    mark_processed(M, uid)
                errors += 1
                continue
            if r.status != "ignore":
                store.log(r)
            mark_processed(M, uid)        # only after the message was handled and logged
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass
    out("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if errors else 0
