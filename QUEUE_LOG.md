# Queue log

## 01 - golden question set and grader (2026-10-25)

What changed
- `eval_golden.json`: 274 questions in 18 categories (courses, teachers, blocks_offerings, course_catalog, ab_days, bell_times, advisory, clubs, sports, calendar_events, links_resources, policies, campus_contact, off_topic_safety, greetings, privacy, multi_part, follow_ups). Each has `expect` (all must match; a nested list means any-of) and `forbid` regexes. About 45 are real questions pulled from `analytics_events` (student names removed), 35 adapted from eval_answers.py, the rest synthetic. Facts were taken from the indexed workbooks/calendar/handbook (checked against a full dump of document_chunks_v2), not from bot output. Date-relative questions are flagged `dynamic` and have deliberately weak expectations.
- `eval_golden.py`: runs the set against a base URL (default http://localhost:8765, or `GOLDEN_BASE`), serial with sleeps, retries 429/5xx with backoff and reports persistent failures as ERROR (not scored), prints a per-category table and failing list, writes `data/eval/golden_<timestamp>.json` (gitignored), exits 1 below `--threshold` (default 0.70). Options: `--category`, `--limit`, `--sleep`, `--verbose`. Matching normalizes no-break spaces, curly quotes and dashes (the model emits them).
- No product code changed.

Baseline (local backend, live Supabase data, 2026-10-25)
- First full run: 224/274 = 82%. About 14 of those failures were my own over-strict expectations (e.g. demanding block/room on "who teaches X"; "A-day" spelling); I corrected those and rescored the same stored answers: **239/274 = 87.2%**. 0 request errors.
- Per category (pass/total): bell_times 6/14 (43%), campus_contact 5/8 (62%), advisory 8/12 (67%), multi_part 6/9 (67%), ab_days 13/17 (76%), links_resources 7/9 (78%), follow_ups 9/11 (82%), courses 39/45 (87%), privacy 8/8 after pattern fix, course_catalog 15/16, clubs 24/25, calendar_events 31/32; teachers, blocks_offerings, sports, policies, off_topic_safety, greetings 100%.

Real failures found (not fixed in this prompt, per instructions)
- Bell times: "What time is Block 2 / Block 4 end / lunch / school day end / how many classes per day / Block 7 on a B' day" answered "not listed" (one said Middle School lunch 11:35). The knowledge/bell_schedule.md facts are retrieved in eval_retrieval but the model still declines.
- Campus facts: front desk phone, Hillsborough address and phone answered "not in the information I have".
- Advisory by number (9.5, 11.1, 11.5) answered "handbook does not list advisors"; "What advisory is he in?" asks for a student name as if it could look one up.
- A/B days: "Which blocks meet on A/B days" not answered; Block 4 on Dec 3 (an A' day) given as 1:55-3:10 but should be 12:35-1:50; Nov 6 (B' day) not identified; follow-up "What about the next day?" after Oct 5 said Oct 6 is not on the calendar.
- Wrong answer: follow-up "Who is the advisor?" for Model UN gave Chris Scott; the clubs catalog says Patrick Berger. "When does Model UN meet?" said not listed (Tuesday lunch, room 1103).
- Courses: Art of Repair, Psychology, Chinese 5, Philosophy of Consciousness (course is "Phil. of Consc/Personhood"), Linguistics teachers not found though present in school_courses. "What languages can I study?" lists only Spanish 2.
- Calendar: Thanksgiving (Nov 26) said "not listed"; US Spring Sports Athlete/Parent Meeting (Feb 1, 2027) not found.
- Links: tour question returned the visit-and-apply page instead of Ravenna; "make a gift" returned a support-nueva page instead of givecampus.

Notes and follow-ups
- Each full run is about 25 minutes (4 s sleep) and uses a lot of Groq tokens; use `--category` for quick checks. The server answer cache makes immediate re-runs fast and identical, so restart the server or wait out the TTL to measure variance.
- Some expectations are intentionally soft where the data is thin (cell phone policy: the indexed phone FAQ is another school's sample; league info; coach).
- Privacy: no emails, rosters, salaries or student contact details appeared in any answer.
- Several dynamic questions will need review when the date moves past the 2026-27 events they reference.

## 02 - drive the golden score up (2026-10-25)

Diagnosis (layer -> fix). The data was present for nearly every failure; the problems were retrieval and prompt, not missing facts, so no knowledge/*.md or pipeline change was needed.
- Bell times, campus phones/addresses, "which blocks meet on A/B days": retrieval ranking. "What time is Block 2?" parsed as a date question, so the 2-week/today digest rows and unrelated `school_day` chunks crowded out the curated bell facts. Fix: `Retriever.pinned_facts` always shows the bell-schedule or campus facts (labelled KEY FACTS, never truncated) when the question matches; `school_day` rows are dropped from vector results when the structured lookup ran; "blok"-style typos handled.
- Advisory 9.5 / 11.1 / 11.5: ranking. New `advisory_lines` extracts the exact "Advisory X.Y: advisors, room" line; the two-week digest is suppressed for these questions.
- Model UN / AMC-AIME / follow-up "Who is the advisor?" (wrong answer Chris Scott): ranking. New `club_chunks` direct lookup (name, acronym in parentheses, aliases such as "Model UN"), also tried against the previous user turn for short follow-ups.
- Art of Repair, Psychology, Chinese 5, Philosophy of Consciousness (and a typo variant), Linguistics: entity lookup required the exact course key as a phrase. Added a loose token matcher (prefix/abbreviation/typo tolerant) used only when the question has a who/teach/room/block cue.
- Nov 6 not identified as B': intent bug. The word "kind" in "what kind of day" became a calendar keyword and matched nothing. Added kind/type/sort to STOP and "kind of day" to the day-type patterns. Follow-up "What about the next day?" now resolves from the previous question's date. Keyword calendar lookups also had an aliasing bug (postgrest builders mutate in place, so the "any keyword" fallback never worked); fixed, with a single-date fallback to that day's schedule.
- Thanksgiving, Spring Sports Athlete/Parent Meeting: intent. Named holidays inside no-school questions are now fetched; "meeting/parent/banquet..." no longer turns an athletics question into a team overview.
- Privacy and "What advisory is he in?", Block-time hedging: prompt. SYSTEM_PERSONA now states the bell times, says KEY FACTS/teaching/club/advisory entries are authoritative, asks for the advisory room, and tells the model to decline student/roster/contact/salary requests in one sentence.
- "What languages can I study?": added a SYNONYMS entry (language -> Chinese/Spanish/French/Japanese/Latin...).
- Grader fixes (not loosening to hide failures): the "not in the catalog" expectation also accepts "does not list/include/contain/mention/have" (the model's correct wording for AlphaGen/quidditch/flag football). Added `--only-failed <golden json>` to eval_golden.py.
- Local-eval aid: `WARMUP_DISABLED=1` skips the startup warm-up so it does not spend LLM tokens.

Scores
- Baseline (prompt 01, rescored): 239/274 = 87.2%.
- Round 1 (rerun of the 50 questions that failed in the first run, current grader): 46/50 pass; the other 224 were not re-asked, so the implied full score is about 270/274 = 98.5%, an estimate.
- Round 2 (fixes for the 4 left over, plus later fixes): only partly measurable, see below. Spot check of 14 questions: 6 passed (Dec 3 Block 4, "blok 3", Spring Sports meeting, Jordan Smith, salaries, Thanksgiving+break), 8 errored with HTTP 429 and are unscored (advisory 9.5/11.5, "advisory is he in", alphagen, fika, advisory 9.1 students, parent email list, "next day").
- Retrieval-level regression check over all 259 non-dynamic golden questions (does the retrieved context contain the expected facts?): old code 223, new code 247, 0 questions regressed, 24 fixed. The remaining 12 misses are privacy/refusal and "not in the data" questions whose expectations are about the answer wording, not the context.
- eval_retrieval.py: 39 -> 60 cases, all pass (new cases for every fix above; follow-up cases use an optional history field).

Not completed / honest limits
- A second full golden run did NOT finish: Groq free tier is 200,000 tokens/day per model (rolling) and 8,000/min; my runs (plus earlier ones) exhausted all three models, after which answers failed with 429. No answers could be read from eval_answers.py for the same reason (gate 4 partly met: /health data_ok true and about 60 answers read across the runs, all sensible; no private data seen). The 8 unscored questions above should be rerun with `python eval_golden.py --only-failed data/eval/golden_20261025_021631.json`, after the quota recovers.
- Prompt-only fixes (privacy wording, advisory room) are unverified for the 8 errored questions.
- These evals spend the same Groq quota as the live site; see NEEDS_USER.md.

Follow-ups
- Cut tokens per answer (contexts are about 3k tokens) so a full golden run fits the free quota, or use a separate Groq key for evals.
- Remaining soft spots: staff-by-last-name only; Spring 2027 offerings not filled in; follow-up resolution relies on one previous user turn.

## 03 - consistent, polished answer formatting (2026-10-25)

Changed
- SYSTEM_PERSONA STYLE block rewritten as one house style: direct answer first; 1-2 sentences / under ~60 words for simple questions; "-" bullets only for 3+ items; no headings; compact "Thu, Oct 22 - 3:45-4:50 PM - Home, place" lines or small tables; dates "Thu, Oct 22", times "8:55-10:10 AM"; bold only 1-2 key facts; `[label](exact-url)` links only from context, never bare; never mention context/sources/file names ("I don't have that" + next step instead); no filler/emoji.
- components/Markdown.tsx: table renderer (horizontal scroll wrapper, compact cells), nested-list spacing, long-word wrapping (`overflow-wrap:anywhere`), links already open in a new tab with rel noopener noreferrer. Checked in the browser pane at 375px: no horizontal page overflow (scrollWidth 375), table fits, long URL and 70-char word wrap, nested bullets fine.
- Streaming: new `stabilize()` used while a message streams hides an unfinished trailing table row / header-only table, a dangling `[label](partial-url`, and unpaired `**`, so half-built markdown never flashes. Unit-checked with 7 partial strings. ChatView passes `streaming`.
- format_lint.py (`python format_lint.py [golden json...]`, `--selftest`): flags too_long, heading_in_short, bare_url, missing_link (how/where to book/register/etc. with no link), source_leak, unclosed_markdown, bullets_under_3, filler_opener, emoji, overbold. Default run merges the newest stored answer per question from data/eval/golden_*.json (274 answers).

Numbers
- Lint on the 274 stored answers (produced under the OLD prompt): 10 violations in 10 answers (bullets_under_3 3, source_leak 3 "the context does not...", overbold 2, bare_url 1, too_long 1). I fixed one lint false positive (noun "book fair" flagged as missing link) so the starting figure was 11 -> 10.
- After: NOT measurable. Groq quota is still exhausted (every live call returned 429 on all three models), so no answers were generated under the new prompt. The prompt rules target each of those 10 categories directly, but this is unverified.
- Gates: eval_retrieval 60/60; py_compile ok; tsc and next build ok; /health data_ok true. Gate 4 answer-reading could not be done (429).

Follow-ups
- When quota recovers: `python eval_golden.py --limit 15 --sleep 15`, then `python format_lint.py <new json>`; expect near-zero.
- Source-leak phrases like "the context" are only prompt-enforced; a server-side rewrite of the final answer could be added if they persist.

## 04 - learn from real usage (2026-10-25)

Data: Supabase analytics_events, last 14 days. Real traffic exists, all from 2026-10-23: 12 visitors, 151 answered chats (8 visitors), 6 thumbs-down, 4 chat_error (all 429), 1 feedback_text ("Test"). Full write-up in data/eval/usage_review_2026-10-25.md (data/ is gitignored, so it stays local).

Changed
- query_server.py: instant canned replies (no retrieval, no LLM) for identity/approval questions ("who made you", "are you school approved"), praise ("good answer") and complaints ("That is wrong"); these took 7-9 s and hit 429 before. The "Who do I contact about <whole question>?" chip now appears only for real policy topics and is phrased from the topic. Prompt: only copy phone numbers/emails/URLs from the context (a local check produced an invented "(415) 555-1234"), and never say "in the current context".
- rag.py: course shorthand aliases ("meche" -> Mechanical Engineering, "comp sci", "econ", ...), and teacher lookup tolerates a misspelled last name when the first name is present ("dan norgren" -> Dan Norgan).
- eval_retrieval.py: 60 -> 68 cases; eval_golden.json: 274 -> 282 (src "usage").
- NEEDS_USER.md: league names, tutorial booking steps, swim, homecoming info, superlatives/AlphaGen, IT help contact, and confirmation of the identity wording.

Numbers
- eval_retrieval: 66/68 before the rag.py fix (the two new cases failed), 68/68 after.
- 44-question retrieval probe of real usage questions: remaining misses are data gaps (swim, wifi password, superlatives, league, AlphaGen), listed in NEEDS_USER.
- Gates: py_compile ok; no frontend changes (tsc ok); /health data_ok true (5897 rows).

Not verified (honest limits)
- Groq returned 429 on almost every live call again, so gate 4 (eval_answers.py) was NOT run and I could not read new answers. Verified live: "Who teaches meche" -> "Mechanical Engineering is taught by Dan Norgan in room 112."; canned replies via curl. Unverified: the no-invented-phone-number prompt line, the new chip phrasing end to end, and the thumbs-down questions (tutorial, block-5 math, sports lists, events this week), which predate the prompt 02/03 fixes.
- Suggested rerun when quota recovers: `python eval_golden.py --category greetings,clubs,teachers --sleep 15` then spot-check "when does nueva swim season start" for a fabricated number.

Follow-ups
- Consider a server-side check that strips phone numbers not present in the retrieved context.
- Daily Groq quota is the main user-visible failure (4 of 4 errors on alpha day).

## 05 - retrieval and chunk quality (2026-10-25)

Measured with new eval_recall.py (retrieval only, no LLM): 236 golden questions with plain-fact expectations; "docs" metrics ignore the structured paths (pinned facts, teacher/club lookups, calendar) and score only vector+rerank results.

| metric | before | after |
|---|---|---|
| recall@5 (all retrieved items) | 1.000 | 1.000 |
| MRR (all items) | 0.910 | 0.911 |
| docs-only recall@5 | 0.508 | 0.542 (0.623 simulated with keyword search, not installed yet) |
| docs-only MRR | 0.422 | 0.459 (0.506 simulated) |
| retrieval p50 / p95 (236 queries) | 416 / 526 ms | 431 / 585 ms (small regression, within run-to-run noise but not nothing) |
| eval_retrieval.py cases | 68/68 | 254/254 (186 added from golden facts; loaded from eval_golden.json) |
| chunks in plan | 5904 | 5376 |

Changed
- eval_retrieval.py: now also loads every golden question with a plain-fact expectation (254 cases). eval_recall.py (new) and sweep_rerank.py (new, offline knob sweep on cached candidates).
- Chunk inspection found: 2112/5904 chunks above the 1100-char context cut, 116 "Student : Directory" contact-card chunks, login-page shells, ~880 near-duplicate chunks (same document imported under two URLs or with #anchors, shown as several identical results), ~850 Nexus Google Doc chunks titled with a bare URL and a BOM. pipeline/chunker.py now: strips BOM/rule lines/UI chrome, derives a title from the first line when the title is a URL, skips login/directory pages, drops near-duplicates (same first AND last 200 normalised chars, best copy kept by source rank). pipeline/config.py: NOISE_TITLE, BOILERPLATE_LINE.
- Re-embedded only changed chunks: 907 + 258 (additive, no --prune, one expired calendar row removed by the indexer as usual). 1,131 superseded rows remain until you prune (NEEDS_USER).
- rag.py: rerank knobs exposed and tuned by sweep (vector weight 0.5 -> 0.3, head boost 2.0 -> 1.0, length exponent 0.25 -> 0.4, outline boost 0.35 -> 0.2), candidates 24 -> 32, runtime dedupe of repeated chunks (dedupe_rows), optional Postgres full-text candidates (fts_search, needs supabase/fts.sql), typo fix for event/sport words ("labr day"), club acronym in parentheses with spaces ("ChemLab Club"), intergrade group lookup ("Where does intergrade Group A meet?"). Race fix: Supabase client is created before worker threads start.
- Tried and not kept: larger head/title boost (worse), calendar penalty (no effect), candidate counts above 32 (no gain).

Not done / honest limits
- Keyword search (FTS) is written but not live; the migration was denied, so only the simulation (local weighted keyword scan) supports the gain.
- Gate 4: eval_answers.py ran against local backend but 31 of 35 answers returned 429, so answer quality was not read. /health data_ok true; one live answer (cell phone policy) was fine.
- Golden expectations are loose regexes, so total recall@5 is near ceiling; the docs-only metric is the informative one. Weight sweep used the same 236 questions it is scored on (mild overfit risk; gains are small and the knobs moved modestly).
- Chunk context cut (1100 chars) still truncates the tail of ~35% of chunks; not changed because larger context costs Groq tokens.

Follow-ups: run supabase/fts.sql, then prune; re-run `python eval_recall.py` to confirm the real FTS numbers.

## 06 - adversarial and privacy testing (2026-10-25)

New: guard.py (pre-LLM safety layer), eval_redteam.py (258 checks). Groq was still returning 429, so everything that needs the model is reported "unverified (429)", never as blocked.

| section | checks | result |
|---|---|---|
| A pre-LLM attacks (77: student/parent/roster/grades, salary, prompt injection incl. fullwidth, zero-width, Cyrillic, letter-spaced, leetspeak; essays/homework; harmful; threats; self-harm) checked locally and through the running backend | 154 | 154 pass (live answer must be the canned text, model_used none, no sources) |
| B false positives (legit policy/advisory/roster-limit/careers/stress questions) | 35 | 35 pass |
| C input handling (empty, whitespace, zero-width-only, 10k chars, 1001, wrong types, long history, 10k anon_id, 200 KB body, bad JSON, bidi, emoji, RTL, zalgo) | 19 | 19 pass |
| D retrieved text: 7 poisoned chunks scrubbed through build_context, record-shaped chunk blocking, 15 live retrieval privacy queries, output leak check, streaming canned replies | 34 | 34 pass |
| E LLM-dependent (roleplay, base64, task-embedded extraction, distress without trigger words, authority claims...) | 16 | 16 unverified (429 on every real model call; 2 earlier run passes were guard-answered) |

Total: 242 pass, 0 fail, 16 unverified. eval_retrieval 254/254; golden set: 7 questions are now answered by the guard and their canned text satisfies the golden expectations.

Changed
- guard.py: sanitize (NFKC, invisible/bidi/control chars removed), canned replies for self-harm (988, 911, counselor/trusted adult, never a refusal), threats, student and staff privacy, injection/key/prompt extraction, essay/homework (policy: one sentence redirect, offer school help, "how do I write" advice still goes to the model), harmful requests; scrub_context removes instruction-like lines from retrieved chunks; leak_check replaces model output containing the system prompt or secrets (also mid-stream).
- query_server.py: guard runs inside smalltalk() so both /api/chat and /stream answer instantly with no LLM; query sanitised by validator (empty after cleaning gives 422); 422 no longer echoes the input; 64 KB body cap; anon_id/session_id length caps; SYSTEM_PERSONA gets a security paragraph (context is data, no essays/code, distress handling).
- rag.py: BLOCKED_CONTENT drops chunks with Home address/phone fields, Student ID numbers, Parent/Guardian contact fields; build_context scrubs injected instructions.
- Index scan: no student data found; details and optional delete SQL in NEEDS_USER.md.

Not done / honest limits
- No attack that reached the LLM could be tested; the LLM prompt paragraph and the output leak check are unverified live. Rerun `python eval_redteam.py http://localhost:8765 --llm-limit 16 --sleep 10` when quota recovers.
- Regex guard is a first layer: paraphrased attacks ("what were you told before this chat?") rely on the prompt. Mid-stream leak check can let the first few words of a leaked prompt through before it cuts off.
- eval_answers.py: 30 of 35 answers 429 again; not read. 
- Guard is English only; an essay request in another language falls to the model.

## 07 - speed and resilience (2026-10-25)

Measured with new bench_speed.py (SSE, by category). Groq quota was still exhausted: almost every real LLM call returned 429, so time-to-first-token and full-answer times for real model answers are NOT measurable (one live call that did succeed: llm_ms 316, ttft 613 ms, total 734 ms; not a sample).

| stage (ms) | local (laptop) | live Render |
|---|---|---|
| smalltalk/canned TTFB = total | 9-22 | 100-120 (network) |
| retrieval to first SSE event (meta), by category | 460-1200 (calendar 513-1205, teacher/course 458-693, docs 512-778, clubs 485-569, links 635-707) | 150-640 |
| server-side embed (edge function) | - | 344 (dominant retrieval stage; vector 72, calendar 88, rerank ~0) |
| cache hit (repeat question) | - | 81 |
| cold start | not measured (service is kept warm by the 10-min ping; a real cold start needs the service asleep) | |
Slowest known stage: the embed Edge Function call (~340 ms of ~420 ms retrieval); LLM stage unmeasured (429).

Changed
- answer_store.py + supabase/answer_cache.sql: persistent L2 answer cache. Key = normalized question, TTL via expires_at (10 min date-bound, 6 h otherwise), no ids, personal-looking questions (email, long digit runs, phone) never stored, writes on a background thread, 2 s read timeout, 120 s circuit breaker, falls back to in-memory L1 if the table is missing or Supabase is slow. Migration applied through the Supabase MCP (it was allowed this time); a live write was observed (l2_writes=1 in /health). Not stored: answers without sources, degraded answers, follow-ups, custom-model requests.
- Warm-up: top 30 questions from the last 30 days of analytics plus starters; loads from L2 instead of regenerating, paces only real LLM calls, stops at the first LLM outage.
- Degradation: if every Groq model fails (429, 5xx, timeout, connection error, 25 s budget, or the stream dies) the visitor gets the retrieved calendar lines, confirmed facts and source links with an honest note, flagged degraded (never cached); a stream that breaks mid-answer keeps the text and says it was cut off. Embedding function 546/5xx or slow vector search: retrieval continues with the structured lookups (calendar, pinned facts, teachers, clubs, links), or returns a friendly "search unavailable" message if nothing matched. Supabase unreachable: friendly message, no stack trace; raw exception text is no longer sent to the browser. Timeouts: DB 10 s, query embedding 8 s x2, per-LLM-call 3-20 s.
- test_resilience.py: 40 simulated drills, all pass (Groq 429 on all models, timeout, connection error, HTTP 500, fallback model, mid-stream break, edge function 546/500/503/timeout, vector search slow/failing, Supabase unreachable, L1 served while Supabase down, L2 round trip/TTL/PII/missing table/slow, restart simulation served from L2 with LLM down, warm-up).
- Memory: local backend RSS 71 MB idle, peak 92 MB under 3 rounds of 20 concurrent streaming requests (60 requests, all HTTP 200, max 3.5 s; mostly degraded answers because of 429). Linux/Render numbers will differ somewhat but there is ample headroom against the ~150 MB target; not measured on Render itself.

Gates: eval_retrieval 254/254; py_compile ok; frontend untouched (tsc/build not run); /health data_ok true. Gate 4: eval_answers.py against the local backend was blocked by 429 (the 15 answers that completed before I stopped it were all degraded fallbacks), so real answer quality was NOT verified; the degraded text format was read and fixed (duplicate bullet dashes, "nothing found" line when only sources existed).

Not done / follow-ups
- Deploy has not happened (branch only): after merge, Render needs no new env vars; optional WARM_TOP, LLM_BUDGET_S, DB_TIMEOUT_S, ANSWER_L2_DISABLED.
- Frontend does not yet style the `degraded` flag in the done event (it renders as a normal answer).
- Re-measure ttft/full-answer by category with `python bench_speed.py https://nuevai-backend.onrender.com --repeat` when Groq quota returns; verify warm-up logs on Render after deploy.
- An optional cleanup of expired answer_cache rows: `delete from public.answer_cache where expires_at < now();` (not run).

## 08 - frontend quality pass (2026-10-25)

Checked Chat, Calendar, Resources, Feedback modal and /insights at 375, 768 and 1440 px in the browser pane (local Next dev server against the local backend).

Measured
- Touch targets under 44px at 375 px (buttons, links, tabs, inputs): Chat 11, Calendar 17, Resources 70+ before; 0 after (only the search inputs inside 48px wrappers, then given a 44px height too). No horizontal overflow at any width before or after.
- Contrast (script, computed colours): only out-of-month calendar day numbers failed (4.36:1, raised by lifting their opacity). Active tab label flagged by the script is a false positive (white on the sliding navy pill).
- Lighthouse 12 (mobile, production build, backend deliberately unreachable): performance 0.96, accessibility 1.00, best-practices 0.96, SEO 1.00. The two soft misses are LCP (0.86) and console errors, which came from /api/warm failing because the test had no backend.

Fixed
- Shell: tabs are a real tablist (roving tabindex, arrow/Home/End keys, aria-controls, tabpanels); tab changes push history so Back/Forward move between tabs (was replaceState); offline banner; status dot has an accessible name; skip link; safe-area insets (viewport-fit=cover); 44px targets.
- Chat: IME-safe Enter (composition Enter no longer sends; Shift+Enter newline), 16px inputs (stops iOS zoom), stop button while streaming, scroll follows the answer only while the reader is at the bottom (no more fighting the user), "jump to latest" button, reduced-motion aware scrolling, screen-reader status line instead of announcing every token, mobile New chat (icon) button (was hidden below 640 px), long unbroken strings wrap and Shift+Enter newlines show in the user bubble, no autofocus on touch screens, shorter follow-up placeholder (it wrapped), subtle note under degraded answers, "Stopped" state. Bug fixed: a refresh while an answer was still empty restored a skeleton forever; also dev StrictMode erased the saved chat on load (saves now wait until the saved chat is read).
- Calendar: "today" and month start use America/Los_Angeles, not the device clock (verified with TZ=Asia/Tokyo: 2026-10-26 12:00 JST is Oct 25 in LA); rolls over at midnight; stale fetch responses ignored when paging months fast; month navigation moves the detail panel to a visible day; tapping a day on phones scrolls to its detail; day cells have aria labels (date, today, event count) and aria-pressed; filters/view toggle have group labels and pressed state; empty-with-filters message and agenda skeleton; event text wraps long strings and descriptions are 13px; .ics export now includes a VTIMEZONE block and a collision-safe UID.
- Resources: search matches every word and also searches the extra links found on school pages; result count announced; duplicate arrow link removed from the tab order; ask buttons no longer 60% opacity.
- Feedback modal: focus trap, Escape, focus returns to the button, page scroll lock, aria-labelledby, safe-area padding, scrollable on short screens, close timer cleared, 16px textarea.
- Insights: main landmark, labelled chart, wrapping long questions instead of truncating, 16px inputs, focus rings.
- Global: focus ring no longer changes the element's radius, `.bare` inputs defer to the wrapper's ring (no double ring), overscroll containment, app/error.tsx (uses `retry`, this Next version's prop name, per node_modules/next/dist/docs) and app/not-found.tsx.

Link checker (plain GET, 22 curated URLs from lib/resources.ts): 21 return 200. givecampus.com returns 403 with a Cloudflare bot challenge (not dead; kept). The Nueva Store link redirects to teamlocker.squadlocker.com (works; left unchanged). Also GET-checked the 45 extra links the backend serves: all 200 except 15 Google Forms that answer 401 (restricted to Nueva sign-in, expected) and the same givecampus 403. Nothing removed.

Gates: eval_retrieval 254/254; tsc clean; next build ok; no Python changed (py_compile and gate 4 not applicable).

Not done / follow-ups: real-device checks (iOS Safari keyboard/safe area, VoiceOver) were not possible here; the chat markdown table/long-answer layout was only checked with one live answer; Lighthouse was run without a backend.

## 09 - conversation quality (2026-10-25)

Finding: the backend already used prior turns (history sent by the client, previous user question prepended to short queries, "next day" date resolution, club follow-ups). It had no real follow-up rewriting and no grade.

Changed
- followup.py (new, no LLM, no network): rewrites a follow-up into a standalone question from the previous user turns. Handles variant swaps ("what about JV?", "and girls?"), bare dates ("and tomorrow?", "what about Friday"), pronouns ("who teaches it?", "what room is that in?", "when do they meet?"), same-question-new-subject ("and Block 4?", "what about Intro to Film + Video?"), attribute-only ("what room?"), chains of follow-ups. Self-contained questions ("and how do I donate?") are left alone. "Next/previous day" stays with intent.py. rag.retrieve uses the rewritten text for entity, club, pinned-fact, link, advisory lookups, vector search and (first) intent parsing; result["resolved"] carries it. No LLM rewrite was added (not needed; saves tokens).
- Context kept short: client sends the last 4 messages (each cut to 700 chars); server uses the last 4 and cuts to 700; when a follow-up was rewritten the system prompt carries one line "treat it as: ...".
- Grade (9-12): optional pill row under the composer, stored only in localStorage (nuevai.grade), sent as `grade` with a request. Server: validated 9-12; only used when the question is grade-sensitive (courses, advisory, graduation, schedule...) to append the grade to retrieval text and a short prompt note; such requests bypass both caches; never put in analytics props, answers cache or logs.
- Suggestion chips: built from the resolved question and the entities actually found (course -> "Who teaches it?"/"Which blocks and rooms?" with the real course name, teacher -> "What else do they teach?", game/practice/day-type specific), and any chip the student already asked (this or earlier turns) is dropped. Grade chips only appear for grade-sensitive questions.
- eval_golden.json: +30 follow_ups cases (history included). eval_retrieval.py dedupe now keys on (question, history) so same-text follow-ups with different history all run.

Measured (offline retrieval, no LLM)
- New 30 follow-up cases: 30/30 pass; with the rewriter disabled (old prev-question concat only) 27/30 (failed: "And varsity?", "Who teaches it?" after a catalog question, "What else does he teach?").
- eval_retrieval: 286/286 (was 254 + 30 new + 2 previously hidden by the dedupe). test_resilience: 40/40. tsc clean, next build ok, py_compile ok.

Unverified
- Gate 4: the local backend returned a few real LLM answers (a follow-up with grade=10 returned "Rob Zomber." for "Who teaches it?" after "What is Intro to CAD?" and chips adapted), then Groq 429s began again so eval_answers.py ended in degraded fallbacks. Live answer quality is NOT verified. "What events are coming up this week?" got "I don't have that" from the model on one run (not related to this change; worth a look when quota returns).
- Grade pills not checked in a browser at 375/1440 widths; touch target 44px set on mobile via classes only.

Follow-ups: run eval_golden.py with the new follow_ups category once Groq quota is back; consider a deploy then check /insights for follow-up error rates.
