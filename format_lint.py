"""Format linter for NuevAI answers. Usage: python format_lint.py [golden_json ...]
Runs over stored golden outputs (no LLM calls). Exit 0 always; prints violation counts."""
import glob, json, re, sys
from collections import Counter

URL = re.compile(r"https?://[^\s)\]>]+")
MDLINK = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
LINK_Q = re.compile(r"\b(sign ?up|register|registration|apply|book|tour|pay|form|link|where (do|can) i|how (do|can) i)\b", re.I)
LEAK = re.compile(r"\b(chunk[_ ]?id|source:|sources:|according to the (context|documentation)|the context|\.pdf\b|\.md\b|school_\w+|\[\d+\]|\bdoc(ument)? \d+)", re.I)
FILLER = re.compile(r"^\s*(sure|certainly|of course|great question|absolutely|happy to help|as an ai|i'd be happy)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def words(s): return len(re.findall(r"\S+", s))


def format_lint(q: str, a: str) -> list[str]:
    v = []
    if not a.strip():
        return v
    n = words(a)
    table = bool(re.search(r"^\s*\|.+\|\s*$", a, re.M))
    bullets = len(re.findall(r"^\s*[-*] ", a, re.M))
    if n > 180 and not (table or bullets >= 4):
        v.append("too_long")
    if re.search(r"^\s*#{1,6} ", a, re.M) and n < 120:
        v.append("heading_in_short")
    # bare URLs: any URL not inside a markdown link
    stripped = MDLINK.sub("", a)
    if URL.search(stripped):
        v.append("bare_url")
    if re.search(r"\b(how|where)\b.*\b(book|register|sign ?up|apply|pay|log ?in|fill)", q, re.I) and not MDLINK.search(a) and not URL.search(a) and not re.search(r"don't have|do not have", a, re.I):
        v.append("missing_link")
    if LEAK.search(a):
        v.append("source_leak")
    if a.count("**") % 2 or a.count("```") % 2 or len(re.findall(r"\[[^\]]*\]\([^)]*$", a, re.M)):
        v.append("unclosed_markdown")
    if bullets == 1 or (bullets == 2 and n < 40):
        v.append("bullets_under_3")
    if FILLER.search(a):
        v.append("filler_opener")
    if EMOJI.search(a):
        v.append("emoji")
    if a.count("**") // 2 > 4:
        v.append("overbold")
    return v


def main():
    files = sys.argv[1:] or sorted(glob.glob("data/eval/golden_*.json"))
    latest = {}                      # newest stored answer per question
    for f in files:
        for r in json.load(open(f))["results"]:
            if r.get("answer"): latest[r["q"]] = r
    tot = Counter(); bad = 0; n = 0; examples = {}
    if True:
        for r in latest.values():
            n += 1
            vs = format_lint(r["q"], r["answer"])
            bad += bool(vs)
            for x in vs:
                tot[x] += 1; examples.setdefault(x, []).append(r["q"])
    print(f"answers={n} with_violations={bad} total={sum(tot.values())}")
    for k, c in tot.most_common():
        print(f"  {k}: {c}  e.g. {examples[k][:3]}")


def selftest():
    cases = [
        ("q", "## Title\nShort.", "heading_in_short"),
        ("q", "Go to https://example.com now.", "bare_url"),
        ("how do I register?", "Fill out the form on the website.", "missing_link"),
        ("q", "The context does not say.", "source_leak"),
        ("q", "It is **bold and not closed.", "unclosed_markdown"),
        ("q", "Sure! Here you go.", "filler_opener"),
        ("q", "- a\n- b", "bullets_under_3"),
        ("q", "word " * 200, "too_long"),
    ]
    for q, a, want in cases:
        assert want in format_lint(q, a), (want, a)
    assert format_lint("how do I register?", "Register [here](https://x.org/r).") == []
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
