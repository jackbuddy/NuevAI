"""Builds vocab_freq.json: how many indexed chunks contain each word. qnorm.py uses it to fix typos ("tommrow", "skool") by snapping a
word that appears nowhere in the school's text to a very close word that appears often. Read-only against the index; rerun after
big data changes (it is cheap, about 6 requests):

    python scripts/build_vocab.py
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import settings  # noqa: E402


def main() -> int:
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    df: Counter = Counter()
    off, rows_n = 0, 0
    while True:
        rows = sb.table(settings.CHUNKS_TABLE).select("content,metadata").range(off, off + 999).execute().data
        for r in rows:
            m = r.get("metadata") or {}
            text = f"{m.get('title', '')} {r['content']}"
            df.update(set(re.findall(r"[a-z][a-z'-]{2,}", text.lower())))
        rows_n += len(rows)
        off += 1000
        if len(rows) < 1000:
            break
    keep = {w: n for w, n in df.items() if n >= 2 and len(w) <= 24}
    out = ROOT / "vocab_freq.json"
    out.write_text(json.dumps(dict(sorted(keep.items())), separators=(",", ":")))
    print(f"{rows_n} rows, {len(df)} distinct words, kept {len(keep)} (>=2 chunks) -> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
