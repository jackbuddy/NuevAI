# 02 - Drive the golden score up

Start from the latest `data/eval/golden_*.json` (run `eval_golden.py` if none). For every failure, diagnose the layer first (data missing / chunking / retrieval ranking / intent parsing / prompt / model hallucination), then fix at the right layer:
- missing or wrong facts -> `knowledge/*.md` or pipeline parsing, then `python run_pipeline.py --stages chunk,index` (use `--stages index --dry-run` first; no `--prune` unless the dry run shows only expected removals)
- ranking -> SYNONYMS / rerank weights in rag.py, with a regression case in eval_retrieval.py
- date/sport/day parsing -> intent.py
- hallucination or format -> SYSTEM_PERSONA
Iterate until the golden score stops improving or after 6 rounds. Never overfit by hardcoding answers to golden questions. Report score per round.
