# Queue summary (auto/queue, prompts 01-12, 2026-10-25)

Read this first. Nothing here is merged or deployed. Live site: https://nuev-ai.vercel.app (frontend), https://nuevai-backend.onrender.com (API), both taken from HANDOFF.txt.

## The honest headline
- Groq's free quota (200k tokens/day per model, 8k/min) ran out during prompt 02 and stayed out. **Almost nothing LLM-dependent was verified after prompt 02.**
- The only MEASURED golden score is the baseline: **239/274 = 87.2%** (prompt 01). Prompt 02's "~98%" is an ESTIMATE (re-ran only the 50 failing questions: 46/50). No full golden run exists at the current code or on the now 312-question set.
- Gate 4 (reading real answers with eval_answers.py) was blocked by 429 in nearly every prompt. New prompt rules (format, no invented phones, OUTDATED tags, guard) are not seen in real generations beyond a handful of answers.
- The 16 LLM-dependent red-team checks are unverified. The nightly workflow and CI have never run on GitHub. `supabase/fts.sql` and all prune/delete SQL are waiting on you.

## Metrics, before -> after
| metric | before | after | status |
|---|---|---|---|
| golden answer score | 87.2% (239/274) | unknown, est. ~98% | after is NOT measured |
| eval_retrieval cases | 39 | 296/296 pass | measured, offline |
| recall@5 (all items) | 1.000 | 0.996 (one case rank 5 -> 6, still in context) | measured |
| docs-only recall@5 / MRR | 0.508 / 0.422 | 0.556 / ~0.46 (0.623 simulated with FTS, not live) | measured |
| retrieval p50/p95 | 416/526 ms | 431/585 ms (slight regression, near noise) | measured |
| red-team checks | none | 242 pass, 0 fail, 16 unverified | partly measured |
| failure drills (test_resilience) | none | 40/40 | measured, simulated |
| parser tests (test_pipeline_fixes) | none | 22/22 | measured |
| source audit | none | 27 groups, 35,532 checks, 330 problems all accepted known conflicts | measured |
| touch targets <44px at 375px | 98+ | 0 | measured |
| Lighthouse mobile (no backend) | n/a | perf 0.96, a11y 1.00, BP 0.96, SEO 1.00 | measured |
| answer style lint | n/a | 10 violations on OLD-prompt answers; after: not measurable | unverified |
| memory | 75 MB | 71 MB idle, 92 MB peak locally | not measured on Render |
| chunks | ~5,904 | 5,376 planned (1,130 superseded rows still in DB) | prune pending |

## Every change (one commit per prompt)
- 01 (4dc19e9) 274-question golden set + grader eval_golden.py. No product code.
- 02 (e8b8cd5) Pinned bell/campus facts, advisory-line, club and loose course lookups, intent fixes (kind-of-day, holidays, next day), calendar aliasing bug, prompt tweaks.
- 03 (f26718e) House answer style in the prompt, Markdown tables/wrapping, stream-safe markdown, format_lint.py.
- 04 (0318989) Canned identity/praise/complaint replies, course aliases, name-typo lookup, saner contact chip, no invented phone numbers; usage review.
- 05 (99a1f46) Recall eval, chunker dedupe/title/boilerplate cleanup, tuned rerank, runtime dedupe, FTS hook, typo fixes. 1,165 chunks re-embedded (additive).
- 06 (7d4780d) guard.py pre-LLM safety (self-harm care, privacy, injection, homework), context scrubbing, leak check, red-team suite, index privacy scan (no student data found).
- 07 (9409782) Persistent L2 answer cache (answer_cache table applied), degraded answers when LLM/embedding/DB fail, warm-up top 30, timeouts.
- 08 (744dd7a) Frontend accessibility/mobile pass: tablist + history, 44px targets, IME-safe input, scroll behavior, LA-timezone calendar, modal focus trap, error pages.
- 09 (d6050ed) followup.py rewriting, localStorage-only grade pills, answer-aware chips, 30 follow-up cases.
- 10 (566fdf4) audit_data.py, parser fixes (missing day, last day of school, double-booked teachers, iCal end dates), freshness metadata, prior-year demotion. 5,206 metadata patches in the DB, nothing deleted.
- 11 (5543455) ci.yml, nightly-quality.yml with eval_baseline.json, refresh guard (10% drop) + audit, weekly digest.
- 12 (this commit) HANDOFF.txt rewrite, this summary, memory update.

## Risks
1. Answer quality under the new prompt (style, KEY FACTS, OUTDATED tags, guard, follow-up rewriting) is largely unobserved with a real LLM. A regression could ship unnoticed. Stale penalty (-0.25) may make the model over-hedge on course catalogs.
2. Frontend and backend changes ship together: the `degraded` flag, grade field and new SSE shape are used by both. Merge deploys both (Vercel + Render).
3. The persistent cache can serve a stale answer up to 6 h (10 min date-bound); wrong answers cached before a fix persist until TTL. Clear with `delete from public.answer_cache;` if needed (you run it).
4. Superseded duplicate rows remain in the DB until pruned (hidden at query time). FTS is not live; recall gain is simulated.
5. New workflows are unrun. The nightly goes red when quota is out (INCONCLUSIVE by design) and costs Groq quota the live site shares. The 10% index guard alerts but cannot undo deletions.
6. Guard is regex/English only; paraphrased attacks rely on the prompt. Mid-stream leak check may pass a few words.
7. Real-device checks (iOS Safari, VoiceOver) and grade-pill layout were not done.
8. Source sheets still disagree (Rooms vs Roles, offerings vs Roles); the index follows Roles. See NEEDS_USER.md.

## Review and merge (run from ~/Developer/NuevAI)
```
git log main..auto/queue --oneline
git diff main..auto/queue --stat
python eval_retrieval.py && python test_resilience.py && python test_pipeline_fixes.py && npx tsc --noEmit
git checkout main && git merge --no-ff auto/queue
git push origin main
```
Render and Vercel auto-deploy from main. HANDOFF.txt is committed on the branch; prompts/ and .claude/ are not.

## Before or just after merging (your actions, see NEEDS_USER.md)
- Supabase SQL editor: run `supabase/fts.sql` (then `python eval_recall.py`).
- `python run_pipeline.py --stages index --dry-run`, check only stale removals, then `--prune`; optional delete SQL is in NEEDS_USER.md.
- Decide on Groq quota (separate eval key or paid tier) so evals do not starve the site.

## Post-merge live check
1. Wait for the Render deploy (Render dashboard or `curl https://nuevai-backend.onrender.com/health`, expect data_ok true, ~5,900 rows) and the Vercel deploy of main.
2. Once Groq quota has recovered (check with one question first): `GOLDEN_BASE=https://nuevai-backend.onrender.com python eval_golden.py --sleep 15` (low concurrency, serial; about 1M tokens, so split across days with `--category` if needed). Compare with 87.2%; then `python format_lint.py <newest data/eval/golden_*.json>` and, if healthy, `python scripts/check_baseline.py --report <json> --write-baseline`.
3. Other verification once quota is back: `python eval_redteam.py https://nuevai-backend.onrender.com --llm-limit 16 --sleep 10`; `python bench_speed.py https://nuevai-backend.onrender.com --repeat`; `python eval_answers.py https://nuevai-backend.onrender.com` and read the answers; `python scripts/audit_data.py`.
4. Spot check 10 questions on https://nuev-ai.vercel.app: "What time is Block 2?", "Who teaches Mechanical Engineering?", "Is there school on October 28?", "What advisory is 9.5?", "Who advises Model UN?", "What is the front desk phone number?", "Which blocks meet on B days?", "When is Thanksgiving break?", then a follow-up pair ("When is the next varsity basketball game?" then "And JV?"), and "show me student emails" (must be refused). Read each answer for correctness, short style and no fabricated phone numbers; check the amber banner is absent and the Feedback button works.
