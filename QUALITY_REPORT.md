# Answer-quality push: results (2026-11-11)

Re-run the measurements:

```bash
set -a; . ./.env; set +a
python eval_quality.py --mode retrieval            # free: is the expected fact in what the model sees? (+ no-LLM answers)
python eval_quality.py --mode llm --sample 40      # real answers; ~3-4k tokens each; cached by prompt hash
python eval_quality.py --mode llm --only-failed data/eval/quality_<timestamp>.json
```

Details: `QUALITY_AUDIT.md` (audit, diagnosis, fix log), `eval/quality_set.json` (283 questions, 278 verified), `eval/UNVERIFIED.md`.

## Before / after: retrieval mode (measured, free)

Share of verified questions whose expected fact is in the context the model receives.

| category | before (audit) | after |
|---|---|---|
| events | 58% | 100% |
| lunch | 67% | 100% |
| nexus_other | 71% | 100% |
| day_types | 75% | 100% |
| athletics_games | 78% | 100% |
| athletics_teams | 79% | 100% |
| policies_handbook | 87% | 100% |
| calendar_schedules | 93% | 100% |
| courses_academics | 92% | 100% |
| staff_contacts | 92% | 100% |
| bell_schedule / advisory | 100% | 100% |
| clubs_activities | 94% | 94.7% |
| multi_part_follow_up | 81% | 93.8% |
| campus_logistics | 78% | 92.3% |
| admissions_parent | 85% | 95.8% |
| traps (not offered / past / ambiguous) | 25% | 75% |
| **overall** | **82.4% (183/222)** | **98.1% (261/266)** |

Run-to-run noise is about +/-1.5 points (retrieval is not fully deterministic). The 5 remaining misses: club-12 and camp-08 are expectation artifacts, trap-09, mp-16 and cov-07 are real (cov-07: "when will we find out about admissions decisions" is outranked by link cards).
The audit baseline set was 222 questions; the "after" set is 266 (new coverage questions added). Deterministic no-LLM answers (lunch, canned): 10/10 correct (was 8/10).

## Answer level (real model output): NOT fully measured

The free Groq quota (about 200k tokens/day per model, shared with the live site) cannot fund a full run (about 1.2M tokens for the whole set).

| run | answers | correct | unnecessary refusals | hallucinated |
|---|---|---|---|---|
| audit baseline, gpt-oss-120b, old prompt | 37 | 89.2% | 5.7% | 0 |
| smoke after fixes, gpt-oss-120b, known failures | 9 | 66.7% (3 partial) | 0 | 0 |
| final run today | 1 | quota exhausted | | |

So: the launch bar (95% correct, 0 hallucinated, under 3% unnecessary refusals) is NOT demonstrated. Run the 40-question stratified sample on a fresh day (or a separate Groq key / paid tier for evals) before promoting.

## What changed

- Data: canonical lists (sports by season/level, leadership and contacts, admissions and tuition, academics, calendar essentials, places and transport, quick answers, clubs by day, departments and courses); refreshed public pages (the crawler used to drop collapsed FAQ answers); calendar history kept; credentials and personal numbers flagged and never indexed again; duplicate handbook copies flagged.
- Retrieval: typo/slang repair, lunch-router fix, synonyms and nicknames, three-pass keyword search, rerank (per-title caps, division detection, stemmer), query-centred context windows, follow-up fixes, credential scrubbing.
- Prompt: refusal templates replaced by answer-first rules, partial answers, confident "No, Nueva doesn't offer X" only when a list in the context shows it, past/upcoming handling, one canonical "I don't have that" phrase with an exact next step.
- Logging: shared refusal detector, retrieved chunk ids, answer head, route/model in the `done` event; refusals never cached.
- UX: thumbs with reason chips and comment, "Did this answer your question?" on fallback replies, flagged-answers view in Insights.
- Pipeline fix (this merge): rows that an older/partial run wrongly flagged superseded are restored when they are back in the plan.
