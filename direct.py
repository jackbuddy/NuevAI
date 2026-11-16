"""No-model answers: when the structured lookup already holds the complete answer, say it straight from the records.

The model is the scarce resource (Groq free tier: tokens per minute), and for a few question shapes it adds nothing:
  - teacher / course questions ("Who teaches Biology?", "What does Max Cowan teach?", "Does Mara Ballmer teach Biology?"):
    the master-schedule records already list every block, room and teacher, including which entries the master schedule does not confirm;
  - day-type questions ("Is there school Monday?", "Is tomorrow an A day or B day?"): one school_day record per date.
Everything here is conservative on purpose: one clear subject, one clear date, a question that asks nothing else, no conversation
history, no low-confidence reading. Anything else returns None and goes to the model exactly as before. Nothing is generated: the
answer is the retrieved record text, reshaped and labelled with its source.

    direct_answer(query, result, history, campus, grade, today) -> {"answer", "sources", "kind"} | None
"""
import os
import re
from datetime import date

import guard
from rag import MULTI_PART, _alias_expand, norm_words

ENABLED = os.getenv("DIRECT_ANSWERS", "1") != "0"
MAX_WORDS = 14

# words a pure teacher / course question may contain besides the names themselves; anything else ("prerequisites", "hard", "AP", "my") sends it to the model
TEACH_WORDS = set("""who whos what which is are does do did the a an in of for to teach teaches teaching taught teacher teachers instructor instructors
class classes course courses block blocks room rooms section sections semester fall spring s mr mrs ms dr prof professor""".split())


def _body(rec: dict) -> str:
    return rec["content"].split("\n\n", 1)[-1].strip()


def _sem(rec: dict) -> str:
    return ((rec.get("metadata") or {}).get("semester") or "").lower()


def _source_line(rec: dict) -> str:
    m = rec.get("metadata") or {}
    src = str(m.get("source_url") or "").replace("workbook://", "") or "the school master schedule"
    when = m.get("source_date")
    try:
        when = date.fromisoformat(when).strftime("%b %-d") if when else ""
    except Exception:
        when = ""
    return f"Source: {src}" + (f" (updated {when})" if when else "") + ". Blocks 1-4 meet on A/A' days and Blocks 5-8 on B/B' days."


def _src(rec: dict, typ: str) -> dict:
    return {"chunk_id": rec.get("chunk_id"), "title": (rec.get("metadata") or {}).get("title"), "type": typ}


# ------------------------------------------------------------------------------------------------------ teachers and courses
def entity_subject(query: str, result: dict, today: date) -> dict | None:
    """The one teacher and/or one course the question explicitly names, with the records of the semester asked for (the current one by default).
    None when the lookup is ambiguous: a first name only, several teachers, several courses, a block listing, a flagged reading."""
    ents = result.get("entities") or []
    ip = result.get("interp")
    if not ents or (ip is not None and (ip.notes or ip.confidence < 0.9)):
        return None
    qn = _alias_expand(norm_words(query))
    toks = qn.split()
    padded = f" {qn} "
    teachers: dict[str, list[dict]] = {}
    courses: dict[str, list[dict]] = {}
    similar: list[dict] = []
    for e in ents:
        m = e.get("metadata") or {}
        kind = m.get("kind")
        if kind == "teacher":
            full = norm_words(m.get("teacher", ""))
            last = m.get("teacher_last", "")
            if not (full and (f" {full} " in padded or (last and len(last) >= 4 and last in toks))):
                return None              # a first-name guess or a fuzzy match: let the model weigh it
            teachers.setdefault(m["teacher"], []).append(e)
        elif kind == "course":
            ck = m.get("course_key", "")
            if ck and f" {ck} " in padded:
                courses.setdefault(ck, []).append(e)
            else:
                similar.append(e)
        else:
            return None                  # block listings, anything new
    if len(courses) > 1:                 # "biology research teams 2" also contains "biology": the longest name the question spells out wins
        longest = max(courses, key=len)
        if all(k in longest for k in courses):
            courses = {longest: courses[longest]}
    if len(teachers) > 1 or len(courses) > 1 or not (teachers or courses):
        return None
    want = "spring" if "spring" in toks else "fall" if "fall" in toks else ("fall" if today.month >= 8 else "spring")
    pick = lambda recs: next((r for r in recs if want in _sem(r)), None)           # noqa: E731
    t_name, t_recs = next(iter(teachers.items()), (None, []))
    c_key, c_recs = next(iter(courses.items()), (None, []))
    t_rec = pick(t_recs) if t_recs else None
    c_rec = pick(c_recs) if c_recs else None
    if (t_recs and not t_rec) or (c_recs and not c_rec):
        return None
    names = set((norm_words(t_name).split() if t_name else []) + (c_key.split() if c_key else []))
    sim_names = sorted({(r.get("metadata") or {}).get("course", "").strip() for r in similar if want in _sem(r)} - {""})
    return {"teacher": t_name, "course": (c_rec.get("metadata") or {}).get("course", "").strip() if c_rec else None,
            "t_rec": t_rec, "c_rec": c_rec, "names": names, "similar": sim_names[:3], "toks": toks, "want": want}


def _caveat(text: str) -> str:
    if "listed only on the course offerings sheet" not in text:
        return ""
    return "Entries marked \"listed only on the course offerings sheet\" are not confirmed by the master schedule."


def teach_answer(query: str, result: dict, today: date) -> dict | None:
    if MULTI_PART.search(query) or len(query.split()) > MAX_WORDS:
        return None
    subj = entity_subject(query, result, today)
    if subj is None:
        return None
    if [t for t in subj["toks"] if t not in TEACH_WORDS and t not in subj["names"] and not t.isdigit()]:
        return None                      # it asks something besides who / which block / which room
    t_rec, c_rec = subj["t_rec"], subj["c_rec"]
    if t_rec and c_rec:                  # "Does Mara Ballmer teach Biology?": her lines for that course, nothing else
        course = subj["course"]
        lines = [ln for ln in _body(t_rec).split("\n")[1:] if re.search(rf":\s*{re.escape(course)}\s*(\(|,|$)", ln, re.I)]
        if not lines:
            return None                  # a "no" is for the model to word (and the department to confirm)
        sem = (t_rec.get("metadata") or {}).get("semester", "")
        confirmed = [ln for ln in lines if "listed only on the course offerings sheet" not in ln]
        lead = (f"Yes. {subj['teacher']} teaches {course} ({sem}):" if confirmed else
                f"Only partly confirmed. The course offerings sheet lists {subj['teacher']} for {course} ({sem}), but the master schedule does not confirm it:")
        body = lead + "\n" + "\n".join(lines)
        recs = [t_rec, c_rec]
    else:
        recs = [t_rec or c_rec]
        body = _body(recs[0])
    parts = [body]
    if cav := _caveat(body):
        parts.append(cav)
    if subj["similar"] and not t_rec:
        parts.append("Courses with similar names you can ask about: " + ", ".join(subj["similar"]) + ".")
    parts.append(_source_line(recs[0]))
    return {"answer": "\n\n".join(parts), "sources": [_src(r, "schedule") for r in recs], "kind": "teach"}


# ------------------------------------------------------------------------------------------------------ is there school / A or B day
DAY_Q = re.compile(r"\b(is there|are there|do we have|will there be|are we having|is it)\b.{0,25}\b(school|class|classes)\b|\b[ab]'?\s*day\b|\b[ab]\s+or\s+[ab]\b|\bwhat type of day\b|\bday type\b|\bwhich day\b", re.I)
DAY_NO = re.compile(r"\b(lunch|menu|games?|practice|sports?|team|advisory|schedule|bell|times?|when|starts?|ends?|homework|exams?|tests?|finals?|assembly|events?|clubs?|early|late|"
                    r"dismiss\w*|bus|after|before|hours?|opens?|closes?|holidays?|breaks?|vacation|thanksgiving|middle|lower|hillsborough|ms|ls|6th|7th|8th|dance|concert|performance|blocks?)\b", re.I)
CAL_CONFLICT = re.compile(r"no school|no classes|school closed|closed|holiday|break|dismiss|late start|early release|minimum day|day off", re.I)


def day_answer(query: str, result: dict, campus: str | None, grade: int | None) -> dict | None:
    if MULTI_PART.search(query) or len(query.split()) > MAX_WORDS or not DAY_Q.search(query) or DAY_NO.search(query):
        return None
    if campus == "hillsborough" or (grade is not None and int(grade) <= 8):
        return None                      # the A/B master schedule is the Upper School's
    it = result.get("intent")
    if it is None or not it.date_given or it.start != it.end or it.sport or it.kind or it.category or it.keywords or it.holidays:
        return None
    iso = it.start.isoformat()
    recs = [e for e in result.get("events") or [] if (e.get("metadata") or {}).get("event_date") == iso]
    days = [e for e in recs if (e.get("metadata") or {}).get("source") == "school_day"]
    if len(days) != 1:
        return None
    rec = days[0]
    m = rec.get("metadata") or {}
    cal = [e for e in recs if (e.get("metadata") or {}).get("source") == "calendar_day"]
    status = _body(rec)
    head = it.start.strftime("%A, %B %-d, %Y")
    status = re.sub(rf"^{re.escape(head)}\s+-\s+", "", status)
    status = re.split(r"\s+Advisory:", status)[0].strip()
    status = re.sub(r"\s*\(Blocks 1-4 meet on A and A'.*$", "", status).strip()
    kind = m.get("kind")
    if cal and any(CAL_CONFLICT.search(e["content"]) for e in cal) and kind == "class_day":
        return None                      # the day calendar mentions a closure / change the A/B sheet does not: the model weighs both
    yesno = bool(re.match(r"\s*(is there|are there|do we have|will there be|are we having)\b", query, re.I))
    if kind == "class_day" and (t := m.get("day_type")) and status.startswith(f"{t} day:"):
        rest = status[len(f"{t} day:"):].strip()
        lead = f"Yes, there is school on {head}. " if yesno else ""
        answer = f"{lead}{'It is' if yesno else head + ' is'} a{'n' if t[0] in 'A' else ''} **{t} day**. {rest}"
    elif kind == "no_classes" and (nc := re.match(r"No classes(?: \((.*?)\))?\.", status)):
        why = f" ({nc.group(1)})" if nc.group(1) else ""
        answer = f"No, there are no classes on {head}{why}." + (status[nc.end():] and " " + status[nc.end():].strip())
    elif kind in ("full_day_event", "short_day"):
        answer = f"{head}: {status}"
    else:
        return None
    answer += "\n\nSource: school day calendar, The Sheet - Master Schedule 2026-27 (Upper School)."
    return {"answer": answer, "sources": [_src(rec, "school_day")], "kind": "day"}


# ------------------------------------------------------------------------------------------------------ entry point
def direct_answer(query: str, result: dict, history=None, campus: str | None = None, grade: int | None = None, today: date | None = None) -> dict | None:
    """-> {"answer", "sources", "kind"} when the records alone answer the question with confidence, else None (the model answers)."""
    if not ENABLED or history or not result or result.get("lunch"):
        return None
    try:
        today = today or result.get("today") or date.today()
        out = teach_answer(query, result, today) or day_answer(query, result, campus, grade)
    except Exception as e:                 # never let a shortcut break a chat
        print(f"direct answer skipped ({type(e).__name__}: {str(e)[:80]})", flush=True)
        return None
    if out:
        out["answer"] = guard.scrub_credentials(out["answer"])
    return out
