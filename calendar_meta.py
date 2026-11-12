"""Which division an event is for, and whether it cancels classes. Shared by the calendar pipeline (stored in each event's metadata), the
/api/calendar endpoint (rows indexed before these fields existed get them computed on the fly) and the tests.

Nueva has three divisions on two campuses: Lower School (PreK-4) and Middle School (5-8) on the Hillsborough campus, Upper School (9-12) in
San Mateo. The school calendar mixes all of them in one feed, and the only marker is the title ("MS Parent Ed", "US P/T Conferences",
"LS Open Stage", "5th-6th Grade ERB Testing", "Class of 2027 ..."). Nothing is guessed beyond what the title says: no marker means "whole school".

    divisions_of("MS Parent Ed: Digital Literacy Night")           -> ["ms"]
    divisions_of("LS/MS Parent Workshop (Grades 3-8)")             -> ["ls", "ms"]
    divisions_of("Admissions Financial Assistance Workshop")       -> []            (the whole school / not tied to a division)
    no_classes_for("No LS & MS Classes: LS & MS Parent/Teacher Conferences")  -> ["ls", "ms"]
    no_classes_for("No School: Presidents' Day")                   -> ["all"]
    no_classes_for("US Fall Play")                                  -> None
"""
import re

DIVS = ("ls", "ms", "us")
_TOKEN = {"ls": "ls", "lower school": "ls", "ms": "ms", "middle school": "ms", "us": "us", "upper school": "us", "high school": "us",
          "hb": None, "sm": None}


def _grade_div(n: int) -> str:
    return "ls" if n <= 4 else "ms" if n <= 8 else "us"


def divisions_of(summary: str) -> list[str]:
    s = " " + re.sub(r"\s+", " ", summary or "") + " "
    found: set[str] = set()
    # explicit division words, alone or joined: "MS", "LS/MS", "LS & MS", "Upper School", "US:"
    for m in re.finditer(r"(?<![A-Za-z])(LS|MS|US)(?:\s*(?:/|&|,|and)\s*(LS|MS|US))*(?![A-Za-z])", s):
        found.update(t.lower() for t in re.findall(r"LS|MS|US", m.group(0)))
    for word, d in (("lower school", "ls"), ("middle school", "ms"), ("upper school", "us"), ("high school", "us")):
        if word in s.lower():
            found.add(d)
    if re.search(r"(?<![A-Za-z])(HB|Hillsborough)(?![A-Za-z])", s):
        found.update(("ls", "ms"))
    if re.search(r"(?<![A-Za-z])(SM campus|San Mateo|\(SM\)|SM Campus)", s):
        found.add("us")
    # kindergarten and PreK are Lower School ("PreK Parent Social", "K Parent Social")
    if re.search(r"\b(pre-?k|pk|kindergarten)\b|(?<![A-Za-z])K(?=\s+(?:parent|coffee|social))", s, re.I if False else 0) or re.search(r"\b(pre-?k|pk|kindergarten)\b", s, re.I):
        found.add("ls")
    # grade numbers: "5th-6th Grade", "Grades 3-8", "Gr. 9", "7th Grade", "8th/9th", "Rising 6th", "4th grade: Rising 5th Grade"
    for m in re.finditer(r"(?:\bgrades?\b|\bgr\.?)\s*(\d{1,2})(?:\s*(?:st|nd|rd|th))?(?:\s*(?:-|–|/|&|and|to)\s*(\d{1,2}))?|"
                         r"(?<![\d:/.-])(\d{1,2})(?:st|nd|rd|th)(?:\s*(?:-|–|/|&|and)\s*(\d{1,2})(?:st|nd|rd|th)?)?\s*(?:grade|gr\b|graders?|parent)", s, re.I):
        a = m.group(1) or m.group(3)
        b = m.group(2) or m.group(4)
        for x in (a, b):
            if x and 1 <= int(x) <= 12:
                found.add(_grade_div(int(x)))
        if a and b and 1 <= int(a) < int(b) <= 12:          # a range covers the divisions in between ("3-8")
            found.update(_grade_div(n) for n in range(int(a), int(b) + 1))
    if re.search(r"\bclass of 20\d\d\b|\bseniors?\b|\bjuniors?\b(?!\s+varsity)|\bsophomores?\b|\bfreshm[ae]n\b|\brising (g|gr\.?|grade )?9\b|\bPSAT\b|\bSAT\b|\bACT\b", s, re.I):
        found.add("us")
    return [d for d in DIVS if d in found]


TEAM_ENTRY = re.compile(r"\b(varsity|jv|junior varsity|basketball|volleyball|soccer|tennis|golf|swim\w*|track|cross.country|flag football|lacrosse|"
                        r"practice|game|match|tournament|scrimmage|athletics)\b", re.I)


def no_classes_for(summary: str):
    """None when the title does not cancel classes; ["all"] for the whole school; otherwise the divisions that have no classes.
    A team-schedule entry ("US Boys Basketball JV - Thanksgiving Break", a note row on a team's sheet) is about the team, not about classes."""
    s = re.sub(r"\s+", " ", summary or "")
    if TEAM_ENTRY.search(s):
        return None
    m = re.search(r"\bno\s+((?:LS|MS|US)(?:\s*(?:/|&|,|and)\s*(?:LS|MS|US))*)\s+(?:classes|school)\b", s, re.I)
    if m:
        return [d for d in DIVS if d in {t.lower() for t in re.findall(r"LS|MS|US", m.group(1), re.I)}]
    m = re.search(r"\bno\s+classes\s+for\s+((?:LS|MS|US)(?:\s*(?:/|&|,|and)\s*(?:LS|MS|US))*)", s, re.I)
    if m:
        return [d for d in DIVS if d in {t.lower() for t in re.findall(r"LS|MS|US", m.group(1), re.I)}]
    if re.search(r"\bno\s+(school|classes)\b|\b(school|campus)\s+closed\b|\bcampus and offices closed\b", s, re.I):
        return ["all"]
    if re.search(r"\b(winter|spring|november|thanksgiving|february|fall|summer)\s+break\b", s, re.I) and not re.search(r"\bbreak(fast)?\s+(club|room)", s, re.I):
        return ["all"]
    return None


def applies_to(division: list[str] | None, campus: str | None) -> bool:
    """Does an event with these divisions concern someone on this campus? san_mateo = Upper School, hillsborough = Lower and Middle School.
    No campus chosen, or an event with no division marker, always applies."""
    if not campus or not division:
        return True
    mine = {"san_mateo": {"us"}, "hillsborough": {"ls", "ms"}}.get(campus)
    return True if mine is None else bool(mine & set(division))
