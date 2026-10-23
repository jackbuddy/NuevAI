"""Compare the download checklist with what is actually in the manual_docs folder.

    python scripts/check_manual_docs.py
Matching ignores case and the file extension, so "US Tennis.pdf" satisfies "US Tennis.pdf" or "US tennis.docx".
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "data" / "pipeline" / "download_checklist.json"
FOLDER = ROOT / "data" / "pipeline" / "manual_docs"


def main() -> int:
    if not CHECK.exists():
        print("Run  python scripts/make_download_checklist.py  first.")
        return 1
    rows = json.loads(CHECK.read_text(encoding="utf-8"))
    have = {p.stem.lower(): p for p in FOLDER.glob("*") if p.is_file() and not p.name.startswith(".")}
    todo = [r for r in rows if r["status"] != "skip"]
    done = [r for r in todo if Path(r["filename"]).stem.lower() in have]
    print(f"{len(done)} of {len(todo)} files present in {FOLDER}\n")
    for r in todo:
        mark = "[x]" if r in done else "[ ]"
        print(f" {mark} {r['filename']:60} {r['kind']:7} {'(review first)' if r['status'] == 'review' else ''}")
    known = {Path(r["filename"]).stem.lower() for r in rows}
    extra = sorted(p.name for k, p in have.items() if k not in known)
    if extra:
        print(f"\nOther files in the folder (will still be indexed): {', '.join(extra)}")
    print("\nNext: python run_pipeline.py --stages manual,chunk,index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
