# NuevAI quality audit (Phase 1 + diagnosis)

Date of audit: 2026-11-11 (branch `quality/audit`, built on `main` @ 3ecd410). Everything below was measured against the live index
(`document_chunks_v2`, 5,384 rows read read-only) and the production code; "measured" vs "estimated" is stated explicitly.
Tools produced: `eval/quality_set.json` (golden set), `eval_quality.py` (grader), `eval/baselines/`, `eval/diagnose_retrieval.py`, `eval/UNVERIFIED.md`.

## 0. Executive summary

1. The data covers most of what students and parents ask. Of 222 retrieval-eligible verified questions, **82.4% have the expected fact in the context the model sees** (measured, free retrieval mode; five runs gave 82.4-85.4%). The misses are retrieval problems, not data gaps (section 5).
2. When the fact IS in the context the model usually answers well, but the prompt pushes it into "I don't have that; contact ..." the moment the context is imperfect (section 6 quotes the exact rules). Real-LLM numbers are in section 4.
3. Hallucination is rare in the sampled answers; the dominant failure modes are overcaution, truncation of the context (a 1,100-character cap per document, a 14-event cap per calendar block) and the structured calendar lookup suppressing better matches.
4. The product cannot currently measure its own refusal rate: analytics store the question but not the answer, and the `fallback` flag is a regex that only catches "is not in/available" (it missed every "I don't have that; contact ..." reply).
5. No data was changed and no product file was modified (see section 8 for the one proven bug and for what I did not touch).

## 1. Architecture audit

### 1.1 Data sources and ingestion (`run_pipeline.py`, `pipeline/`)
| Stage | Source | Code | Freshness (measured 2026-11-11) |
|---|---|---|---|
| public | nuevaschool.org crawl, once | `pipeline/crawl_public.py` | 93+2 rows, indexed 2026-10-25, content from the Oct 22 crawl; never refreshed (by owner decision) |
| nexus | scraper output from `~/Developer/nexus-scraper` (needs manual SSO) | `pipeline/nexus_import.py` | 1,807+21 rows, all stamped 2026-10-25, page dates 2026-09-04 (two months old); handbook pages, resource board, course catalogs |
| google | linked Google Docs/Sheets/Slides | `pipeline/fetch_google.py` | 1,272+88+78+23 rows, all 2026-10-25 |
| manual | files the owner exports by hand (handbook PDF text, catalog) | `pipeline/manual_import.py` | 285 rows, 2026-10-25 |
| workbooks | "The Sheet" master schedule, Clubs Catalog, Course Offerings | `pipeline/workbooks.py`, `live_sheets.py` | school_day 215 days (Aug 17 2026-Jun 11 2027), courses 256+135+23, clubs 119, advisory 6; refreshed by hand (2026-10-25) |
| calendar | private iCal feed (`NUEVAI_CALENDAR_URL`) | `pipeline/calendar_feed.py` | 291 events + 203 day digests, refreshed daily (last 2026-11-09 23:06 UTC); **history starts 2026-10-27 (rolling 14-day window, `KEEP_PAST_DAYS`)** |
| live team sheets | US basketball, volleyball sheets | `pipeline/live_sheets.py` | 82 games + 42 events, last 2026-11-05 |
| lunch | cafe PDFs via the cafe's Google calendar | `pipeline/lunch.py` | Oct + Nov 2026 for both campuses (65 menu-day rows + 21 no-menu-day rows), last 2026-10-28; December not posted yet |
| email | forwarded school emails (IMAP) | `pipeline/email_ingest.py` | 26 chunks, Oct 23-Nov 8 2026 |
| links | classified links, pinned booking/payment | `pipeline/links.py` | 53 rows |
| facts | `knowledge/*.md` (bell schedule, campuses) | `pipeline/facts.py` | 4 rows |

Chunking (`pipeline/chunker.py`, `config.py:CHUNK_TARGET_CHARS=1100`, `CHUNK_MAX_CHARS=1600`): measured **median 885 characters, 1,828 of 5,384 rows are over 1,100 characters (max 4,315)**. Rows are embedded whole (gte-small, 384-d, via the Supabase `embed` edge function), but `rag.build_context` shows only `d["content"][:1100]` (rag.py:702) so for a third of the rows the model never sees the tail.

Index shape (measured): `nexus` 1,828, `google_doc` 1,360, `calendar` (event/practice/game) 456, `manual` 285, `school_courses` 414, `calendar_day` 203, `school_day` 215, `school_clubs` 123, `public_site` 95, `calendar_sheet` 124, `google_sheet` 83, `lunch` 86, `link_registry` 53, `email` 26, `google_slides` 23, `school_facts` 4, `school_advisory` 6. 982 rows are tagged `stale` (prior-year documents). `answer_cache` 128 rows, `analytics_events` 1,630 rows, `email_ingest_log` 12.

### 1.2 Retrieval (`rag.py`)
Order of operations in `Retriever.retrieve` (rag.py:488-610): typo fix (`fix_typos`, only 28 `DOMAIN_WORDS`, rag.py:90) -> follow-up rewrite (`followup.py`, uses only the previous *user* turn) -> lunch short-circuit (`parse_lunch`, exact day record, no vector search) -> `parse_intent` (calendar/sport/gender/day type) -> in parallel: embed + `match_chunks_v2` RPC (32 candidates), structured calendar lookup, link cards, FTS (`search_chunks_fts`, 12 candidates, **now live**: the RPC exists in the database) -> teacher/course entity lookup, pinned facts (bell schedule/campus), advisory lines, club lookup -> filters (blocked titles/content, superseded, rows without `indexed_at`) -> `rerank` (0.3 vector + 0.7 idf-weighted lexical overlap, outline boost, stale penalty -0.25, older-version penalty, email recency boost) -> `dedupe_rows` -> top 5 docs (+ up to 3 link rows) -> `build_context` (events capped at 14, docs at 1,100 chars).

Strengths: structured lookups (day types, lunch, clubs, courses, advisories) are exact; the 32-candidate pool plus keyword rerank rescues many name queries; stale and email handling is thoughtful.
Weaknesses found are in sections 5 and 7.

### 1.3 System prompt, refusal and fallback logic (`query_server.py`)
`SYSTEM_PERSONA` (query_server.py:252-290) is ~1,900 tokens of rules; each answer costs about **3.1k tokens** (measured: 9,303 tokens for 3 answers). Refusal-shaping rules are quoted in section 6. Degraded path (`degraded_answer`, ~query_server.py:468) builds an answer from retrieved calendar lines/facts when all models are rate-limited; lunch has a deterministic plain-text answer. Model chain `openai/gpt-oss-120b -> gpt-oss-20b -> qwen3.8-27b` (`FALLBACK_MODELS`, query_server.py:488): cached production answers show **qwen answered real questions** (e.g. "how do i start a club") with noticeably different style and leaks like "in the provided context", which the prompt forbids.
Output post-processing: `guard.leak_check`, `with_lunch_note`. No post-generation fact check exists (a server-side strip of phone numbers absent from context is listed as "not built" in HANDOFF.txt section 7.7).

### 1.4 guard.py, caching, entry points
- `guard.py`: sanitize, canned replies (self-harm, privacy, injection, homework), `scrub_context`, `leak_check`, record-shape blocking. Regex/English only. It does not stop a credential stored in an indexed chunk (finding F-8).
- Caching: L1 `TTLCache` (800 items, 6 h; 10 min for date-bound), L2 Supabase `answer_cache` (6 h), key = normalized question + date + model (`_cache_key`, query_server.py:596). Answers that *contain a refusal but have sources* are cached for 6 hours: the cache currently holds e.g. "I don't have that. Please specify which course or class..." (qwen, 2026-11-01) and "No events are listed on the school calendar for this week" (2026-10-26) (measured in `answer_cache`).
- Frontend entry points (not audited in depth; another agent owns them): `/api/chat/stream` (Next route proxy) -> `query_server.chat_stream`, `/api/chat`, `/api/calendar`, `/api/links`, `/lunch`, `/api/track`, `/api/metrics*`.

### 1.5 Fragile / stale / broken / half-built (with refs)
| # | Finding | Where | Severity |
|---|---|---|---|
| F-1 | Context builder truncates every document to 1,100 chars and the calendar block to the first 14 matches by date; the relevant event (e.g. the Mar 11 Shakespeare performance, #15 of 31) or the answer sentence after char 1,100 is invisible. | rag.py:668, 702 | high |
| F-2 | When the structured calendar lookup returns *any* event, all vector-retrieved `calendar`/`school_day` rows are discarded, so a cross-date question ("Is there school Nov 27 and when do classes resume") loses the second date. | rag.py:588 | high |
| F-3 | Typos outside 28 hard-coded words are not corrected ("tommrow", "shedule" survive): `Is tommrow a or b` returns no intent and no day record; `Whats for lunch tommrow` is not recognised as a lunch question. | rag.py:90-103, intent.py `tomorrow` handling (intent.py:90) | high |
| F-4 | Follow-up rewriting looks only at the previous user turn and fires for short wording; `And the varsity girls?` after a boys-basketball answer retrieves the handbook instead of the girls schedule. | followup.py:84-110 | medium |
| F-5 | `fallback` analytics flag only matches "isn't/is not/not (in|available)" and "not on the school calendar" (query_server.py:691, 856): none of the model's actual refusal phrasings ("I don't have that; contact ...") are counted, so the dashboard's "questions it struggled with" is nearly empty (4 flagged of 220 real chats). | query_server.py:691,856 | high (blind spot) |
| F-6 | Handbook exists in 4 copies (nexus 261 rows, google_doc 441, manual 200) = 902 of 5,384 rows (17%); 610 doc rows sit in duplicate groups. The nexus copies carry **no section path** (0/261 have `Section:`), so "The school day begins at 8:10 / ends 3:15" (Middle School) and "8:55 / 3:10" (Upper School) look identical in context. | chunker, `pipeline/nexus_import.py` | high |
| F-7 | The calendar keeps only 14 days of history (`KEEP_PAST_DAYS = 14`, pipeline/calendar_feed.py:24), so the index starts at 2026-10-27; `lunch_lookup` hides October for lunch although the rows exist (rag.py `_unavailable_line`); "was X" questions about Aug-Oct can only be answered from school emails. | pipeline/calendar_feed.py:24 | medium |
| F-8 | A shared bus-app username+password sits in a Nexus resource-board chunk (`8d2146f2eaca-0016`). The prompt does not forbid printing passwords; item `trap-13` probes it. | index data | medium (privacy) |
| F-9 | `Retriever.retrieve` filters `current = rows with indexed_at or non-doc source`; staff/leadership questions ("head of school", "division head") depend on 2024-25 news bylines because the staff directory cards were removed (privacy) and no leadership fact exists. | rag.py:584 | medium |
| F-10 | `rag.build_context` marks `[OUTDATED...]` text and demotes it with `STALE_PENALTY` (rag.py:30), but 982 rows are stale and many of the best answers for policy topics (e.g. club guidelines 2023-24, Visual Arts catalog 2025-26) come from stale docs. The prompt tells the model to hedge ("say it may have changed"). | rag.py:30, 700 | medium |
| F-11 | No IT help, tutorial-booking, tuition, MS clubs, league, results, coach-roster or leadership data (section 2). | data | high (coverage) |
| F-12 | `SYSTEM_PERSONA` has two near-duplicate "if the answer is not in the context" rules (query_server.py:265 and :283) and a third for the calendar (:271). | query_server.py | high (causes overcaution) |
| F-13 | Nexus content collected once on 2026-09-04 (page dates) and indexed 2026-10-25: Resource Board "Physical Education Requirements", season dates etc. still show 2024-25 items; CI/nightly workflows never ran on GitHub (HANDOFF section 2). | pipeline | medium |
| F-14 | The persistent cache can freeze a refusal or a model-specific answer for 6 hours; there is no negative-answer exclusion beyond "no sources". | query_server.py:690-697 | medium |

## 2. Data-coverage inventory (what is indexed, how much, how fresh, what is missing)

| Topic | Indexed (measured) | Freshness | Missing for a Nexus-replacement |
|---|---|---|---|
| Calendar / day types | 291 events + 203 day digests (Oct 27 2026-Jul 23 2027); 215 `school_day` records with A/A'/B/B' type, advisory schedule, full-day/no-class reasons | events daily, day types hand-refreshed 2026-10-25 | history before Oct 27; MS/LS day schedules (day types are Upper School only); the feed's "Veterans Day"-style holidays are info-only |
| Bell schedule | pinned KEY FACTS (US blocks 1-8, lunch, A'/B' meaning); US 8:55-3:10 | confirmed by school | MS/LS bell times exist only in handbook text (no section path in nexus copy); special-schedule day times (assemblies, Community 40') are not modelled |
| Athletics teams | handbook varsity offerings by season (US) and MS offerings; PSAL/WBAL leagues; PE requirement; staff page (directors, strength coach) | handbook "limited updates 1/1/2026"; Nexus board stale | coach roster, JV/varsity levels by sport, tryouts/cuts, fees/forms steps, season start dates (2027), swim/tennis/golf/cross-country/track information beyond one sentence |
| Athletics games | US boys basketball varsity (30) + JV (25), girls basketball (23), girls volleyball (4, Oct only), MS basketball + girls soccer (all levels, 82 games), via sheets/calendar | sheets refreshed daily | **no tennis, golf, cross-country, swim, spring sports schedules; no results/scores (only 2 in emails); no game venues for US basketball (location column empty)** |
| Clubs/activities | 119 clubs with advisor, meeting day/time/room, description, student leaders (kept by owner) | 2026-27, 2026-10-25 | MS clubs (the Nexus page only), club meeting time TBD for several, how to join/sign up |
| Staff/teachers/contacts | 135 teacher records (courses/blocks/rooms), 6 advisory records, athletics staff page, campus phones/addresses, nurse email | schedule Fall 2026 + Spring 2027 draft | directory (filtered out for privacy), leadership (head, division heads, deans, counselors), IT help contact, counseling contacts, department chairs |
| Policies / handbook | handbook x4 copies (902 rows): attendance, tardies, open campus, dress code, AI/academic integrity, lice, OTC medication, lost and found, transportation outline; US cell phone policy (9 rows) | handbook "limited updates 1/1/2026" | Honor Code text (only a slide mention), late-work/grading policy not found, discipline process detail |
| Lunch | Oct + Nov 2026 menus both campuses (cafe PDFs) | 2026-10-28 | December+ (posted by the cafe later), allergens/dietary tags (cafe provides icons only), PreK-K menu |
| Events | calendar entries (arts, parent ed, workshops, admissions dates); Nueva Notes and One-Stop Shopping emails (26 chunks) | emails to Nov 8 | past events before Oct 27, event details beyond the calendar blurb, RSVP/ticket info |
| Admissions / parent info | link registry (Ravenna tours, $100 fee, donation, store), admissions dates (deadline Jan 5, decisions Mar 12, responses Mar 19), financial-assistance workshop dates/platform | calendar daily; pages 2026-09-04 | tuition figure, US visit-and-apply page is the 2025-26 version, FAQ, after-school care pricing |
| Campus logistics | addresses/phones, Caltrain GoPass entry (title), parking form, bus ridership agreement, lost and found (Hillsborough) | handbook | San Mateo parking rules and drop-off, maps, visitor policy, building hours |
| Courses / academics | 2026-27 catalog (232+204+190 rows in 3 copies), teachers/rooms/blocks, 2025-26 and 2024-25 catalogs (stale) | Fall 2026 firm, Spring 2027 draft | prerequisites in structured form, grad requirements (2024-25 text only), counselor guidance |
| Advisory | all advisories by grade with advisor names and rooms, intergrade groups, BTSN locations, daily advisory times | 2026-10-25 | (rosters correctly absent) |
| Nexus-type content (grades, schedule, tutorials, library, forms) | resource-board entries by title only; form links; "How To Guide for Students"; tutorial policy text | 2026-09-04 | grades, student schedule, assignments (portal data, never indexed), tutorial booking steps, library hours/catalog, all forms as steps |

## 3. Usage mining (`analytics_events`, read-only)

Corpus: 1,630 events, 543 `chat_answered`, of which **220 real-user questions** from 14 browsers (323 are `golden-*` test traffic; only 10 rows carry the new `_aud` stamp). One browser (`84fda0f6...`, 117 of the 220) is the owner's long-lived browser (internal-probable; `_aud=internal` on its newest rows, 7 stamped "internal", 3 "user"); the other 13 browsers asked 103 questions (one is a classmate group that asked about ethics bowl, mahjong, pinball, senior superlatives). `analytics_audience_overrides` is empty.

Logged failures (every one):
- `fallback` flag (regex): 4 chats (all owner): "classes for mechanisms and building" (multi-part), "salary", "On the calendar is shows a fika", "Who teaches philsophy of consioucness".
- `no_context`: 2: lunch questions ("ive never seen this before Sandwich Favorite..." pasted answer text; "Whats for lunch tommrow").
- Errors: 4 `chat_error` (429 rate limit, Oct 23 05:28-05:30, owner) + 3 auto retries + 1 UI error ("who made you").
- Thumbs-down (9): "How to shedule a tutorial with teacher", "How to join pinball club", "what math classes are block 5 tommrow", "What events are happening this week?", "What sports are playing this season?" (cached), "What are all the upper school sports", "are you a chatgpt wrapper", "When are the next boys basketball practices", "Varsity only".
- Free-text feedback (3): "[deploy check]", "Test", and one real: "change the club catalog to this year's" (club catalog is now the 2026-27 workbook).
- Questions that look like redirects (inferred from topic, answers are not logged): "whose the best teacher", "how long has dan norgren worked here", "salary", "wifi password", "how much does my teacher make", "what happened with senior superlatives?", "who is jack budde", "what league is girls softball in", "when does nueva swim season start", "did nueva flag football win yesterday", "Can my 8th grader have a pager?".
Root cause grouping of the logged failures: (a) rate limits (4 errors, Oct 23: quota), (b) typos and wording the intent parser misses (tommrow, philsophy, fika), (c) missing data (swim/flag football/league names/senior superlatives/tutorial booking), (d) multi-part questions where the first clause retrieves and the second does not, (e) correctly refused privacy/off-topic questions (salary, best teacher), (f) metric blind spot (answers are not stored, the refusal regex misses 'I don't have that').
Cached production answers (`answer_cache`, 128 rows, mostly warm-up of popular questions): 7 contain refusal/redirect language (5.5%); they include model fallbacks (qwen/20b) and the 6-hour persistence problem. This is the only sample of real production answer text (everything else is questions only).

Prior known failure lists mined: `data/eval/golden_20261025_000731.json` (old prompt, 50 failures of 274 = 81.75%; clusters: "the provided information doesn't list..." for bell times, A/B days, advisory, campus phones; teacher lookups; privacy phrasing), `NEEDS_USER.md` (league names, tutorial booking, swim, homecoming, senior superlatives/AlphaGen, wifi/IT contact, identity wording), `QUEUE_LOG.md`/`QUEUE_SUMMARY.md` (87.2% measured, 98% estimated; golden ran out of quota).
Observation: the pinned KEY FACTS, advisory lines and teaching assignments added since Oct 25 fixed most of those 50 (the same questions now succeed in retrieval mode: bell, advisory, campus phones all 100%).

## 4. Golden set, harness and baseline (all MEASURED unless marked)

### 4.1 What was built
- `eval/quality_set.json`: **241 questions**, 234 verified (7 unverified, listed with reasons in `eval/UNVERIFIED.md`). Each verified item carries `verify_checks` (patterns found in the cited source chunks) and no_such_thing items carry `absent_checks` (patterns that match no chunk in the whole index). Built by `eval/build_quality_set.py` (also the way to add items).
  - By category: admissions_parent 14 (13 verified), advisory 6 (6 verified), athletics_games 23 (23 verified), athletics_teams 21 (19 verified), bell_schedule 9 (9 verified), calendar_schedules 27 (27 verified), campus_logistics 9 (9 verified), clubs_activities 17 (17 verified), courses_academics 12 (12 verified), day_types 12 (12 verified), events 12 (12 verified), lunch 10 (9 verified), multi_part_follow_up 16 (16 verified), nexus_other 10 (9 verified), policies_handbook 16 (15 verified), staff_contacts 14 (13 verified), traps 13 (13 verified).
  - By persona: community_member 18 (18 verified), current_parent 56 (52 verified), ms_student 18 (18 verified), prospective_family 30 (29 verified), us_student 119 (117 verified) (MS questions send `campus=hillsborough`; a few lunch questions send `san_mateo`).
  - By behavior: ambiguous 4 (4 verified), answer 189 (188 verified), no_such_thing 6 (6 verified), partial 21 (16 verified), past_event 9 (8 verified), redirect_ok 4 (4 verified), safety 8 (8 verified).
  - By phrasing: abbreviation 10 (10 verified), clean 193 (186 verified), follow_up 8 (8 verified), multi_part 9 (9 verified), slang 9 (9 verified), typo 7 (7 verified), vague 5 (5 verified).
  - 57 items are date-dependent (`dynamic`); 12 have a `resolver` (day type, lunch dish, next game/event) that recomputes the expectation from the index at run time; the rest are valid only until their `valid_through` date and are reported as `stale_dynamic` afterwards (never as passes).
- `eval_quality.py`: modes `retrieval` (free) and `llm` (cached by sha256 of prompt+context+question+model, token budget, TPM pacing, per-minute 429 backoff, graceful stop with `ungraded_quota`), `--regrade` (re-grade stored answers with the current grader, free), `--self-test` (19 grader unit tests), exit code 1 below the launch bar. Documented in README.
- Deterministic grader: six labels, hedge/refusal detector (REFUSAL vs REDIRECT, severity by behavior, lunch note ignored, "for more details" tail pointer recorded but not an over-hedge), groundedness check (dates incl. ranges, times, phones, emails, URLs, rooms, scores, money, large numbers, and names found nowhere in the index; compared with the retrieved context + question + history + derived dates), behavior rules for no_such_thing / past_event / ambiguous / partial / redirect_ok / safety.

### 4.2 Baseline numbers
Retrieval mode (free, every verified retrieval-eligible question, 222 of 234; 12 safety/redirect items are not applicable): **183/222 = 82.4% retrievable** (five runs gave 85.4%, 82.9%, 84.2%, 83.3%, 82.4%: retrieval is not fully deterministic, the ANN search and FTS ties move 3-8 borderline items between runs). By category (final run): advisory 100%, bell 100%, clubs 94%, calendar 93%, staff 92%, courses 92%, policies 87%, admissions 85%, multi-part 81%, athletics_teams 79%, athletics_games 78%, campus 78%, day_types 75%, nexus 71%, lunch 67%, events 58%, traps 25%. The no-LLM deterministic answers (lunch plain answer, canned replies) graded 8 correct / 2 wrong (both wrong ones are the lunch-router bug, section 5.4).

LLM mode (real Groq answers on the exact production prompt and context; **a stratified sample, not the full set**: a full 241-question run costs ~750k tokens, three days of one model's free quota):
| model | status | graded | correct | over-hedged | unnecessary refusal | partial | wrong | hallucinated | tokens spent |
|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | **primary** | 37 | 33 (89.2%) | 0 | 2 (5.7%) | 1 | 1 | 0 | 124,359 (+9,303 in a 3-question pilot = 133,662, 67% of one day's 200k) |
| openai/gpt-oss-20b | non-primary | 38 | 29 (76.3%) | 0 | 4 (11.4%) | 2 | 3 | 0 | 124,417 |
| qwen/qwen3.8-27b | non-primary | 36 | 27 (77.8%) | 1 | 4 (12.1%) | 1 | 3 | 0 (+1 composite, see 4.3) | 124,400 |
| pooled | | 111 (91 distinct questions) | 89 + 1 over-hedged = 90 (81.1%) | 1 (1.1% of correct) | 10 (9.7%) | 4 | 7 | 0 | 373,176 |
The sampled runs stopped at the self-imposed 128k token budget per model (9/6/8 items left `ungraded_quota`, never counted as passes). The pilot also showed the primary model answered "Does Nueva have a football team?" with "I don't have that; contact athletics@nuevaschool.org ..." (unnecessary refusal on a no_such_thing question).
Primary model by persona: prospective_family 7/7, ms_student 3/3, community 2/2, current_parent 4/5, us_student 17/20. By phrasing: clean 28/31, follow_up 3/3, typo 0/1. By behavior: past_event 0/1, answer 29/32. Cells are 1-10 items: treat as direction, not as scores.
Rates the owner asked for (primary, sample): over-hedge 0% of correct answers (the model hedges by *refusing*, not by padding), unnecessary refusal 5.7% (target < 3%), hallucination 0 flagged, correct 89.2% (target 95%). **Launch bar: FAIL** (retrieval 82.4%, sample 89.2% correct, 5.7% refusals). A 37-answer sample has a ~95% interval of roughly 75-96% for the correct rate, so "FAIL" on correctness is likely but the refusal-rate gap is the clearer signal.
Not measured: the full-set LLM score; real-user refusal rate (answers are not logged); streaming path and cache layers; frontend; guard beyond 8 items; the 7 unverified items; Hillsborough/MS and typo/slang phrasing with n > 3 per cell; paid-tier models.

### 4.3 Grader calibration (I read 111 real answers)
I read every one of the 111 answers (37 + 38 + 36, all categories, 3 models) against the verified sources and recorded my own label (`eval/calibration.json`).
- **Before calibration: 94/111 = 84.7% exact agreement, 96/111 = 86.5% on pass/fail.** The 17 disagreements were systematic and are fixed in the committed grader/set: (1) four false hallucination flags: a date followed by a time (`Nov 16 - 6:00 PM` parsed as a date range), a grade range (`grades 10-12` parsed as a score), and a bare-number hour (`Fridays 4-6 PM` when the source says `4-6`); (2) nine over-strict expectations (demanding a room, location or time the question did not ask for, a date range written as `March 10 - 12`, `office-hours` vs `office hours`, a list question requiring two named examples); (3) one hedge false positive (`For more details` after a complete answer; now a recorded "tail pointer", not an over-hedge); (4) two misses the grader cannot see: a model recombining facts (qwen listed a second "advisory 11.1: Ting Zhang and Brett McCabe, room 345", both names exist in the context but the pairing does not) and an invented consequence ("detention", 20b); the first now has a `forbid`, composition errors in general are not detectable by a groundedness check.
- **After calibration: 108/111 = 97.3% exact, 111/111 on pass/fail. This is in-sample (I tuned on these answers), so the honest figure is the pre-calibration 84.7%/86.5%, plus the expectation that new items will still expose a few strict patterns.** Remaining disagreements: two homecoming answers (`wrong` vs my `partial`), one composite hallucination labelled `wrong` by a forbid.
- Hedge detector: all 11 refusal/redirect answers I identified were caught; one hedge (qwen "contact athletics@...") classified correct_overhedged as intended; no false negatives found on the 10 refusals. Hallucination detector: 0 true positives in 111 answers (the one composite hallucination was missed), 4 false positives before the fixes, 0 after.
- Known grader limits: it checks surface specifics, not logic; it cannot detect a correct-looking claim built from two unrelated true facts; `expect` patterns are regexes, so a correct paraphrase can be marked missing (the set uses alternatives to limit this).

## 5. Failure diagnosis

### 5.1 Verdict in one paragraph
No failure in the verified set is a **data gap**: every expected fact exists in the index (that is what "verified" means). The 39 retrieval misses and all 22 non-correct sampled answers trace to retrieval/routing, the context builder, a few bugs, and model behavior, with the prompt turning each miss into a refusal. Evidence (retrieval status taken from the final free run, joined to the 103 sampled answers that have one; the borderline items move between runs, so treat as +-2): when the expected fact **was** in the context **81/88 (92.0%) were correct and 0 were unnecessary refusals**; when it **was not** in the context, **10 of 15 (67%) were unnecessary refusals, 3 were wrong and 2 correct**. So "I don't know" is not the model being timid with good context; it is the model faithfully following SYSTEM_PERSONA's rule when retrieval hands it the wrong chunks. Fixing retrieval and softening those rules attacks the same failure from both ends.

### 5.2 The 39 retrieval-mode misses (free mode, `eval/diagnose_retrieval.py`)
| Root cause | n | Items | Evidence |
|---|---|---|---|
| Retrieval miss: no keyword/synonym path (answer chunk not among the 32 vector candidates, FTS did not rescue it) | 14 | cal-20 (SAT), pol-14 (honor code vs "Academic Integrity"), ev-07 and trap-10 (homecoming vs "HoCo"), crs-05 (AP classes), adm-02 (application fee: no link intent), ev-02 (Guild), gm-11 (Priory score in an email), trap-09 (spring schedule), mp-16 (email practice times), day-11 (Veterans Day -> day record), lun-06 (Thanksgiving lunch), ev-05 (AMC 10/12), day-02 (typo) | e.g. cal-20: structured calendar intent "upcoming" returned 31 events with no keyword filter; the SAT row `cal-42621576de2c` is outside the vector top 32 |
| Retrieval miss: answer chunk was a vector candidate (rank 1-22 of 32) but lost in rerank/dedupe/top-5 | 15 | day-08, ath-06, ath-07, ath-17, staff-10, pol-08 (lunch router), lun-03 (lunch router), lun-09, camp-07, camp-08, nx-04, trap-05, mp-08, gm-21, gm-20 (FTS-only) | the 0.7-weight lexical rerank and three duplicate copies of the handbook crowd the top 5: handbook copies fill slots for 46% of questions and the Nexus Resource Board hub for 12%; e.g. trap-05: the athletics offerings chunk was rank 1 in vector search yet absent from the final 5 |
| Context builder cut the answer (doc 1,100-char cap; 14-event cap; `events[:3]`) | 3 | gm-18, ms-09, ms-08 | ms-08: the Shakespeare event is #15 of 31 structured matches, only 14 shown |
| Borderline / run-dependent (the chunk is retrieved in some runs and not others, or the context holds the fact in another form) | 7 | ev-08, adm-08, nx-03, mp-03, cal-21, club-17, ath-20 | counted as retrieval risk, not as product failures |
Within the 14 + 15 retrieval misses, the specific mechanisms are: typos outside `DOMAIN_WORDS` (day-02, lun-03, cal-21, bell-09 in other runs), intent mis-route (ev-05 "10/12" parsed as a date; lun-03/lun-09/pol-08 lunch router), nicknames/acronyms (HoCo, AP, SAT, AMC), missing query rewrite for follow-ups (mp-08, gm-18), proper nouns absent from the calendar keyword path (Guild), small email/link chunks losing to long handbook chunks, and duplicates (3 handbook copies) consuming top-k.

### 5.3 The 22 non-correct sampled LLM answers (pooled, 111 answers)
| Root cause | n | Items (model) | Notes |
|---|---|---|---|
| Retrieval miss -> refusal or wrong answer | 10 | ev-05, ev-02 (20b), lun-09 (20b), day-02 (20b), camp-07 (qwen: an unsupported "Yes"), staff-10 (qwen), ms-08 (qwen), lun-06 (qwen), ev-07 (primary, qwen) | every one is in the section 5.2 table; prompt rules (section 6) convert the miss into "I don't have that; contact ..." (ms-08 even asserts "no 7th grade Shakespeare performance is listed") |
| Prompt overcaution on a no_such_thing question | 1 (+1 pilot) | ath-06 (20b); ath-05 (primary, 3-question pilot, not in the 111) | the offerings list was not in the top 5, and the prompt has no "answer a confident No" rule; both said "I don't have that; contact athletics..." |
| Bug | 3 | lun-03 (primary, qwen: lunch router treats "tommrow" as a menu keyword), club-13 (20b: empty completion returned to the user) | section 5.4 |
| Model completeness / instruction-following | 3 | bell-05 (primary: gave only the B' time for "Block 7"), pol-07 (20b: dropped the 10-absence email), adv-05 (20b: no privacy sentence) | context contained the answer |
| Hallucination by recombination / invention | 2 | adv-01 (qwen second advisory group), pol-03 (20b "detention") | not detectable by a specifics check; qwen and 20b only |
| Freshness / time error | 2 | gm-13 (20b skipped today's 3:45 PM game), ms-02 (qwen listed the past Oct 30 conference as upcoming and omitted Feb 5) | the prompt says "never present a past event as upcoming" but nothing tells the model today's events are still upcoming |
| Over-hedge | 1 | mp-09 (qwen "For current team offerings ... contact athletics@...") | copied from the handbook text itself |
Pooled by primary cause: retrieval 10 (45%), bugs 3 (14%), model completeness/hallucination 5 (23%), freshness 2 (9%), prompt overcaution 1 (5%), over-hedge 1 (5%). Every overcaution-style refusal sits on a retrieval miss, so the prompt amplifies rather than originates them. The 20b and qwen rows are non-primary models and are weaker than the primary (76-78% vs 89% correct).

### 5.4 Bugs that I could prove (not fixed here)
- **B-1 lunch router hijacks non-menu questions.** `intent.parse_lunch` + `rag.lunch_data` treat unknown words as menu keywords. "Can freshmen leave campus for lunch?" is answered ONLY from the lunch lookup ("no day in the posted menus lists 'freshmen leave'"), so the handbook's open-campus rule (pol-08) can never be seen; "Whats for lunch tommrow" searches the menu for the word "tommrow" (lun-03, 2/2 models refused). Repro: `Env().prepare(item)` in `eval_quality.py` (route = lunch) then `rag.lunch_plain_answer`.
- **B-2 "AMC 10/12" is parsed as October 12** (`intent.parse_range`, label "October 12", ev-05); the structured lookup then returns one unrelated event.
- **B-3 empty completions reach the user.** `_answer_inner` uses `completion.choices[0].message.content or ""` (query_server.py:673) and does not retry or fall through to the next model; with `max_tokens=600` and gpt-oss reasoning, 1 of 111 sampled answers was empty (club-13, 20b).
- **B-4 analytics refusal blind spot** (query_server.py:691, 856), F-5.
- **B-5 `answer_cache` stores refusals and non-primary-model answers for 6 hours** (e.g. qwen "what block" -> "I don't have that. Please specify...").
I did NOT change any product file. These are bugs by reproduction; B-1..B-3 are small regex/guard changes the fix agents can make with eval cases already in the set (pol-08 is not in the sampled run but fails in retrieval for the lunch route; lun-03, ev-05, club-13 are in the baseline).

## 6. Prompt text responsible for the overcaution (`query_server.py` SYSTEM_PERSONA)
1. L265: `- When unsure or the answer is missing: "I don't have that" plus the best next step in the same sentence (the relevant office, its phone number, or a link from the context). Do not guess.` This is the template of every refusal observed ("I don't have that; contact ...", all 10 unnecessary refusals begin "I don't have ...").
2. L262: `Say "I don't have that" instead of "the context does not say".` Hands the model the refusal string.
3. L283: `- If the answer is not in the context: say what is missing in one sentence and point to the right office or portal. Do not guess.` (duplicate of L265; on a retrieval miss both fire).
4. L271: `- If the calendar block says nothing matched, say that no such event is on the school calendar yet and suggest checking with the relevant office; do not guess.` Produces confident false negatives ("no such event") when the structured lookup merely missed (ms-08, and the 2026-10-26 cached answer "No events are listed on the school calendar for this week").
5. L278: `... when the answer is there, state it plainly and never say it is missing or send the user elsewhere.` is the only anti-refusal rule and it covers just KEY FACTS, teaching assignments, clubs and advisory: everything else has no counterweight.
6. Missing rules: no instruction to answer "No, Nueva does not offer X" from an offerings list (ath-05/06 refused), none to say a past event "already happened" with its date (ev-07), none to treat the calendar's events for *today* as upcoming (gm-13), none to give both B and B' times when the day type is unknown (bell-05), none to prefer a partial answer over a redirect.
7. The weakest-model effect: the same prompt makes qwen/20b leak "provided context"/"the handbook" (forbidden by L262) and still refuse; the prompt is ~1,900 tokens, i.e. 60% of every request, and long rule lists are exactly what small fallback models follow unevenly.
## 7. Prioritized FIX PLAN (nothing below is implemented; measure each step with `eval_quality.py`)

Order = expected gain on the launch bar per unit of risk. P1 items are prompt/builder changes that need no new data.

**P1. Remove the prompt rules that turn imperfect context into a redirect** (`query_server.py` SYSTEM_PERSONA).
Quote and replace:
- :265 `"When unsure or the answer is missing: "I don't have that" plus the best next step ..."` and :283 `"If the answer is not in the context: say what is missing in one sentence and point to the right office or portal."` (the same rule twice) and :271 `"If the calendar block says nothing matched, say that no such event is on the school calendar yet and suggest checking with the relevant office"`.
  Replace with one rule: first look for a partial answer in the context and give it; say "I don't have X" only for the specific missing detail, name the nearest true fact, and give a redirect only when the missing fact is something an office can supply (never "check with the office" for things the context states). Add an explicit negative rule: for "Does Nueva have X?" with a seasonal/club/offering list in the context, answer "No, Nueva does not offer X; it offers ..." (the `no_such_thing` behavior); add a past-event rule: "if the date is before today say it already happened on <date>".
- :262 `Say "I don't have that" instead of "the context does not say"` trains the exact refusal string; remove the quoted example.
- The mandatory lunch note ("check with the cafe for ingredients or allergens") is fine (grader ignores it) but the 'diet' branch in `rag.lunch_context` adds a second hedge: merge.
Expected effect: the unnecessary-refusal rate (section 4) is dominated by this.

**P2. Context builder: stop dropping the answer after retrieval succeeds** (`rag.py:653-720`).
- Replace `d["content"][:1100]` (rag.py:702) with a window around the best-matching sentences (query-term-centred, up to ~1,600 chars) or raise to the chunker max (1,600); 1,828 of 5,384 rows exceed 1,100.
- Replace `events[:max_events]` (14, by date) with relevance-first selection when the intent has a keyword/sport/kind: sort by keyword score, then date; always include events whose summary matches a query keyword. (ms-08: the answer was #15 of 31.)
- For "next game" questions (`events[:3]`), skip non-game rows ("First Day of Practice") before taking 3 (gm-18).
- Keep calendar/school_day rows from the vector search even when structured events exist, but dedupe by chunk id (rag.py:588) so a second date in the same question (mp-03: Nov 27 and Nov 30) survives.

**P3. Retrieval changes** (`rag.py`, `intent.py`, `followup.py`):
1. Typos/slang: extend `fix_typos` beyond 28 words: add a generic pass for common misspellings (tommrow/tomorow/tmrw -> tomorrow, shedule -> schedule, wen -> when, skool -> school, blok -> block, brake after break context) and any token within edit distance 1-2 of a vocabulary built from index titles/club names/teacher names (the index vocabulary is ~25k words; `eval_quality.index_vocab()` already builds it). Fixes day-02, lun-03, cal-21/22.
2. Lunch routing: `intent.parse_lunch` treats unknown words as menu keywords ("freshmen leave", "tommrow", "pizza") (intent.py:217-300). Require a food noun or a lunch-menu cue for keyword search; never route a question to the lunch lookup when it matches `NOT_MENU` plus policy words (pol-08 "Can freshmen leave campus for lunch?" currently gets ONLY a lunch context and cannot be answered). Also treat "is there pizza at ... on Nov 12" and "is there lunch on Thanksgiving" as day-record lookups.
3. Date parsing: "AMC 10/12" parses as October 12 (intent label "October 12", ev-05). Ignore `m/d` when preceded by a competition/test token (AMC, SAT, ACT, AP) or when a proper noun with digits is present.
4. Calendar keyword events: when no date/sport is parsed, run a keyword match on `metadata->>summary` for capitalised words in the question (SAT, Guild, Fika, Homecoming) before falling back to the vector search (cal-20, ev-02).
5. Nicknames/synonyms: add to `SYNONYMS` (rag.py:63): `honor code -> academic integrity plagiarism`, `homecoming|hoco -> HoCo spirit week dance`, `ap (classes|courses|exams) -> AP exams not designed to prepare`, `application fee -> Ravenna $100 family information form`, `division head|head of school -> Division Head Head of School`, `park(ing)? -> parking request form Gr. 11 & 12`, `report ... absence -> US Attendance Reporting Form attendance reporting`.
6. Link cards: extend `LINK_INTENTS` (rag.py:45) with `apply|application|fee|admission` -> the Ravenna cards (adm-02, adm-08), and `parking|locker|work permit|absence form` -> form cards.
7. Follow-ups: `followup.rewrite_followup` should carry the previous *answer subject* (and sport/level) for "and the girls?" / "varsity only"; look back up to 3 user turns and also scan the last assistant turn for a team/date noun (mp-08, gm-18).
8. Rerank: handbook copies (three titles for the same text) fill top-5 slots for 46% of questions and the Nexus "Student : Resource Board" hub (156 rows, a lexical sink) for 12% (measured over 224 retrieval-eligible questions): add a per-title cap (max 2 rows per title) and a title-group cap for the handbook copies so the athletics handbook, forms and sentence-level answers are not crowded out (ath-06/07/17, camp-07, nx-04).
9. Raise `top_k` from 5 to 6-7 for list questions (`LIST_WORDS`) and use FTS candidates (now live) as protected slots: reserve 2 slots for the top FTS hits that contain a rare query token (names: Priory, Summit Shasta, HoCo).
10. Chunking: give the nexus handbook copies the same `Section:` path as the google_doc copies (0/261 have it), or drop the nexus copies of the handbook entirely (902 handbook rows = 17% of the index, 610 near-duplicates); this also stops MS vs US times being confusable.

**P4. Data to add (from real, existing sources)**
| Gap | Source that exists | Where it goes |
|---|---|---|
| Leadership/staff roles (head of school, division heads, deans, directors of admissions/college counseling/athletics, nurse, IT) | Faculty & Staff Directory page (public, `public_site` rows exist but cards removed) + staff page | add a curated `knowledge/leadership.md` (roles only, no personal numbers) |
| Coach roster by team/level | athletics sheets (the owner already has the basketball workbook; add a Coaches tab) | `pipeline/live_sheets.py` tab, `school_athletics` kind=coach |
| All sports/teams by season, level, gender (canonical list) | handbook (US varsity offerings), MS offerings, Resource Board, PE form: three versions disagree (golf fall girls vs spring coed) | one reconciled `knowledge/athletics.md` confirmed by the athletics office; replaces three overlapping sources |
| Tennis, golf, cross-country, swim, spring game schedules | Google sheets per sport (like the basketball/volleyball tabs); `athletics-calendar` page linked in `link_registry` | extend `LIVE_SHEETS` |
| Results/scores | school emails ("One-Stop Shopping", athletics rep) | already ingested; no change needed except retrieval (gm-11, gm-20) |
| Tuition, fees, financial-aid summary | public "Affording a Nueva Education" and "Tuition" pages | refresh the public crawl for /admissions pages |
| IT help contact, tutorial booking steps, league names, MS clubs | see `NEEDS_USER.md` and `eval/UNVERIFIED.md` | owner-provided snippets in `knowledge/*.md` |
| Past events before Oct 27 | school emails (partial), last year's calendar | accept the gap; make the prompt say "that date has passed" with the date, not a refusal |
| Late-work / grading policy, Honor Code text | handbook "Academic Integrity", department docs | confirm with the school |

**P5. Observability (needed to know the real refusal rate)**: replace the analytics `fallback` regex (query_server.py:691, 856) with the hedge/refusal detector from `eval_quality.hedge_info` (store only booleans: `refusal`, `redirect`, `ungrounded_specifics`), log the retrieved chunk ids/titles and `route` for every non-cached answer, and log `answer_head` (first 160 chars, privacy-scrubbed, same retention as the query). Without this the dashboard cannot show the overcorrection the owner sees.

**P6. Model robustness**: qwen3.8-27b answered production questions with different style and some unsupported claims ("Yes, seniors can park on the San Mateo Campus" when the data only has a parking request form). Either drop qwen from `LLM_FALLBACKS` or give it a shorter, stricter prompt; do not cache qwen/20b answers for 6 hours (cache them for the shorter date-bound TTL).

**P7. Safety/data hygiene**: remove or rewrite the chunk containing a shared bus-app login (`8d2146f2eaca-0016`, finding F-8) and add "never output passwords or login credentials" to the guard (`guard.RECORD_CONTENT` or a context scrub for `Password:` lines).

**P8. Calendar history and lunch for past dates**: `pipeline/calendar_feed.py:24 KEEP_PAST_DAYS = 14` drops events older than two weeks, which is why the index starts at Oct 27; raise it to the start of the school year (events are small) so "when was X" questions about this fall are answerable, and let `lunch_lookup` answer any posted month still in the index.

**P9. Cache policy**: do not store answers that match the refusal detector in L1/L2 (or store with a 5-minute TTL); store the model name; never serve a qwen/20b answer from the persistent cache to the primary model's users.

**P10. Re-measure**: after P1-P3, run `python eval_quality.py --mode retrieval` (free) until retrievable >= 95%, then `--mode llm --only-failed <report>` (cached answers make iteration cheap) and a full stratified run split across days (see the cost table in section 4).

**Canonical lists the fixes need (sources exist):**
- Sports/teams by season/level/gender: Upper School varsity (handbook, "limited updates 1/1/2026"): Fall Girls Volleyball, Girls Tennis, Girls & Boys Cross-Country; Winter Boys Basketball, Girls Basketball, Boys Soccer, Girls Soccer; Spring Boys Volleyball, Boys Tennis, Coed Golf, Coed Swimming & Diving, Coed Track & Field, Girls Beach Volleyball. Middle School (handbook): Fall Coed Flag Football, Coed Cross Country, Coed Swim, Coed Golf; Early Winter Girls Soccer, Boys' Basketball; Late Winter Girls Basketball, Boys' Soccer; Spring Girls Volleyball, Coed Track and Field, Tennis. Levels seen on the calendar: US Varsity/JV; MS 7th/8th VA, VB, 5th/6th JVA, JVB1, JVB2, JVC. Conflicts to settle with the athletics office: golf (girls fall vs coed spring), MS "Tennis" vs "Coed Tennis".
- Clubs: the 119 records in `school_clubs` (outline row lists all with meeting times); MS clubs: none.
- Departments/courses: the 2026-27 Course Catalog outline (Computer Science, Economics, Engineering/Fabrication/Design, English, History, Interdisciplinary, Languages [Chinese 1-5, Japanese 1-4, Spanish 1-4], Math, Performing Arts, Science, SEL, Visual Arts).
- Staff directory by role: Faculty & Staff Directory (public site), athletics staff page, `school_courses` teacher records (135), `school_advisory` (advisors).

## 8. What I changed and did not change
Changed (all new files plus a README section): `eval_quality.py`, `eval/` (set, builder, diagnose tool, baselines, UNVERIFIED.md, calibration.json), `QUALITY_AUDIT.md`, `README.md`. No product behavior file was modified; no database write; secrets were never printed. Existing `eval_retrieval.py` still passes (323/323). Tokens spent: 373,176 across three models in the sample runs plus 9,303 in a pilot (about 67% of one day's quota on the primary).


## 9. Fix log (branch worktree-agent-a956fc62ab8414208, 2026-11-11)

Retrieval-mode measurements (free, `python eval_quality.py --mode retrieval`; run-to-run variance 0.4-1.5 points):
| state | set | retrievable | notes |
|---|---|---|---|
| audit baseline (original code) | 222 eligible | 183/222 = 82.4% (five runs 82.4-85.4%); rerun today 186/222 = 83.8% | |
| after the retrieval fixes (typos, lunch router, synonyms, FTS rare + pair search, caps, windows, event cap, division, follow-ups, holiday dates, credential scrub) | same 222 | 214-215/222 = 96.4-96.8% (three runs) | deterministic no-LLM answers 10/10 correct (was 8/10) |
| after merging the DATA agent's branch (new live rows, golden set 283) | 266 eligible | 249, 252, 254, 254/266 = 93.6, 94.7, 95.5, 95.5% (last two identical) | most of the 12 remaining misses are new cov-* items whose canonical fact rows are `superseded` in the live index (below) |

By category before (audit, 222) -> after (266 incl. new items): advisory 100->100, bell 100->100, clubs 94->95, calendar 93->100, staff 92->88 (new cov items), courses 92->100, policies 87->100, admissions 85->83 (new cov items: tuition, decisions), multi-part 81->94, athletics_teams 79->96, athletics_games 78->100, campus 78->92, day_types 75->100, nexus 71->100, lunch 67->100, events 58->100, traps 25->75.

Kept (each measured by retrieval mode and/or a regression case in eval_retrieval.py, now 346 cases):
- `qnorm.py` + `vocab_freq.json`: slang table + vocabulary typo snapping (unknown word, same first letter, distance 1-2, target in >= 8 chunks, never inflections of known words, never proper nouns, never 4-letter words). Fixes day-02, lun-03, cal-21/22, bell-09, gm-21. Rejected first version: unrestricted snapping turned 'baseball' into 'basketball', 'advisor' into 'advisory', 'beat' into 'best'; the guards came from running every question of the set through it (test_quality_fixes asserts clean words are untouched).
- Lunch router: policy/logistics wording ('leave campus', 'allowed', 'can freshmen'), holidays are decided before the menu lookup; food noun + date/campus is a menu question. pol-08, lun-03, lun-06, lun-09 (test_lunch.py unchanged and green).
- Dates: 'AMC 10/12' no longer parses as a date; acronyms (SAT, AMC) become calendar keywords; named holidays resolve to this school year's date (+/- 1 day) and the day record is fetched (day-08, day-11).
- Synonym/nickname table (HoCo, honor code, AP, SAT/ACT, application fee, head of school, parking, absence report, PE, JV, pre-participation forms, offerings lists, first day of school, leaving campus), link intents (apply / application fee -> Ravenna card, allowed through the login filter), amount chunks boosted for 'how much'.
- FTS: broad OR + rare-words OR + (rarest AND (next words)); two protected slots for rows carrying the rarest name/acronym (df <= 25); one retry on 'Server disconnected' (also for the vector call); a transient FTS error pauses keyword search 30 s instead of disabling it until restart (a real bug: one blip switched it off for the life of the process).
- Rerank: blended local/global idf, light stemmer, rare-word boost, division bonus/penalty, school_clubs preference for club questions, day records demoted when the question names no date, per-title cap (2; handbook family 3; exempt for rows >= 85% of the best score), fuzzy shingle dedupe of handbook copies cut at different places (pol-05).
- Context builder: query-centred window up to 1,300 body characters (sentences/lines/list items; long comma lists are windowed too), relevance-first event cap (ms-08), next-game skips non-games (gm-18), small event sets include their descriptions (ev-02), `[Upper/Middle/Lower School document]` labels and an AUDIENCE line only when a conflicting division is present.
- Follow-ups: multi-variant swaps ('and the varsity girls?'), JV means the Upper School.
- Prompt, cache/log/rescue changes (section 10); credential scrub in guard.py (context and answers).
Rejected / not done: raising top_k (token cost; unnecessary once duplicates are removed); a bigger window for every chunk (median chunk is 885 characters); an eval override for camp-08 ('yes' can never appear in a context: counted as a miss, honestly).

Still missing in retrieval mode (12 of 266): club-12 ('119 listed' is a count phrase), adm-11/cov-02/adm-14 (amounts sit in a long table chunk of a page where prose chunks of the same page outrank it), camp-08 (eval artefact), trap-09, mp-16 (needs the email with practice times), cov-07, cov-17, cov-23, cov-25, cov-28 (canonical fact rows, see below).

## 10. Answer behaviour, logging and what the LLM measured
Prompt (query_server.SYSTEM_PERSONA, 9.9k characters vs 8.0k before): the four refusal templates the audit quoted (L262 'Say I don't have that instead of...', L265, L271, L283) are gone. New ANSWERING block: answer first and confidently; partial answer first then say exactly what is missing; confident 'No, Nueva doesn't offer X' only when a list in the context would show X (never on thin context); past events 'already happened on <date>', today's events are upcoming; one canonical phrase for the genuine gap ('I don't have that' / 'I don't have <thing>') plus the exact office/person/page/phone from the context, never invented; freshness remark for changeable facts; division rule; never invent facts; never write passwords. The calendar-miss rule now says 'I don't have <event> on the school calendar' instead of 'no such event exists'. House style, lunch, email, privacy, security rules and the leak-guard anchors are intact (test_quality_fixes asserts them).
Server: refusals/hedged answers are never cached; non-primary answers 5 min in memory only; empty completions are retried (bigger max_tokens, then next model, then the degraded answer); `chat_answered` gets refusal flags from `refusal.py` (the same detector eval_quality.py uses), `rroute`, `div`, `chunks`, `answer_head`; `done` carries `route`, `model`, `limited`; limited answers send no suggestions (the client keeps its Browse Resources / What can I ask? chips), degraded answers send Try again / What can I ask? / calendar chips.
Token cost: the smoke below spent 37.4k tokens on 9 graded answers = about 4.2k per answer (audit: 3.1k) because of the longer persona and the longer windows; a full 283-question run would cost about 1.2M tokens. Trimming the persona is the first lever if quota is tight.
LLM smoke (MEASURED; primary model gpt-oss-120b only; 12 selected known failures, 9 graded, 3 stale/dynamic; 37,404 tokens): correct 6/9 (66.7%), partial 3, wrong 0, unnecessary refusals 0, hallucinated 0. Partial: bell-05 (gave one block time), ath-05 (right confident No, did not name flag football), gm-13 (offered a Middle School game for 'next soccer game'). Previously failing, now correct: ath-06, pol-08, pol-14, lun-03, day-02. With 9 answers the 95% interval for 6/9 is roughly 31-91%: this shows direction (no refusals) and nothing more. NOT measured: any stratified sample, other models, the streaming path against Groq, full-set rates. The launch bar for the answer layer is therefore not shown to be met.
