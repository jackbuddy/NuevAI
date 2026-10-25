#!/usr/bin/env python
"""Compare an eval_golden.py report with eval_baseline.json; exit 1 on a regression.

    python scripts/check_baseline.py --report data/eval/golden_X.json
    python scripts/check_baseline.py --report golden.json --write-baseline   # record a new MEASURED baseline

Fails when score < baseline - tolerance, or when too many questions errored (rate limits/timeouts) to say anything
(graded < min_graded_fraction of all). Prints no secrets: only scores, categories and questions from the golden set.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "eval_baseline.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args()
    rep = json.load(open(a.report))
    base = json.load(open(a.baseline))
    graded, errors = rep["graded"], rep["errors"]
    total = graded + errors
    score = rep["score"]
    floor = round(base["score"] - base["tolerance"], 4)
    print(f"score {score:.3f} ({rep['passed']}/{graded} graded, {errors} errored of {total}); "
          f"baseline {base['score']:.3f} ({base['score_source'][:40]}); floor {floor:.3f}")
    for c, v in sorted(rep.get("by_category", {}).items(), key=lambda kv: kv[1]["pass"] / max(1, kv[1]["total"])):
        if v["pass"] < v["total"]:
            print(f"  {c:20} {v['pass']}/{v['total']}")
    if a.write_baseline:
        if errors > 0.1 * total:
            print("refusing to write a baseline from a run with more than 10% errors")
            return 1
        base["score"] = score
        base["score_source"] = f"MEASURED: {rep['passed']}/{graded} on {rep['when']} against {rep['base']}"
        json.dump(base, open(a.baseline, "w"), indent=2)
        print("baseline updated")
        return 0
    if total == 0 or graded < base["min_graded_fraction"] * total:
        print(f"INCONCLUSIVE: only {graded}/{total} questions were graded (rate limits or backend down). Failing so someone looks.")
        return 1
    if score < floor:
        print(f"REGRESSION: {score:.3f} is below the floor {floor:.3f}")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
