"""Content that must never be indexed: shared credentials, meeting passcodes, personal cell numbers.

The chunker runs every page through `scrub()` so a login written on a resource-board post ("Username: x  Password: y"), a
meeting recording passcode or a staff member's personal cell number never becomes a retrievable chunk. `find()` is the read-only
detector used by scripts/flag_sensitive.py (to flag rows already in the index) and scripts/audit_data.py (to prove none remain).

Only specific, value-bearing patterns are matched: the generic word "password" in policy text ("never share your password",
"user name and password for the account") is not a leak and is left alone.
"""
import re

REDACTED = "[removed: credential]"

# (label, regex). Each regex matches ONLY the secret-bearing span so scrub() can cut it out and keep the rest of the sentence.
PATTERNS = [
    ("username-password", re.compile(r"(?i)\buser\s?name\s*[:=]\s*\S+\s+(?:and\s+)?password\s*[:=]\s*\S+")),
    ("password", re.compile(r"(?i)\b(?:password|passcode|passwd|pass\s?phrase)\s*(?:is\s*)?[:=]\s*[^\s,;)\]]{3,}")),
    ("meeting-passcode", re.compile(r"(?i)\(\s*passcode\s*:[^)]{3,}\)")),
    ("url-password", re.compile(r"(?i)([?&])(?:pwd|passcode|password)=[^\s&)\]]+")),
    ("personal-phone", re.compile(r"\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\s*\((?:cell|mobile|personal)\)", re.I)),
    ("wifi-key", re.compile(r"(?i)\bwi-?fi\s+(?:password|key|passphrase)\s*(?:is\s*)?[:=]\s*\S+")),
]


def find(text: str) -> list[str]:
    """Labels of every sensitive pattern present in text (empty list = clean)."""
    return sorted({label for label, rx in PATTERNS if rx.search(text or "")})


def scrub(text: str) -> tuple[str, list[str]]:
    """-> (text with the secret spans cut out, labels found). A URL password parameter is dropped with its separator."""
    hits = []
    for label, rx in PATTERNS:
        if label == "url-password":
            new = rx.sub("", text)
        elif label == "personal-phone":
            new = rx.sub("", text)
        else:
            new = rx.sub(REDACTED, text)
        if new != text:
            hits.append(label)
            text = new
    return text, hits
