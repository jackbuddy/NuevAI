# NuevAI

A question-answering assistant for The Nueva School community: courses, the school calendar and game
schedules, policies, and campus resources.

```
app/ components/ lib/      Next.js frontend (Vercel): Chat, Calendar, Resources, /insights metrics
query_server.py            FastAPI backend (Render, Dockerfile): streaming chat, calendar + links APIs, metrics
rag.py intent.py cache.py  retrieval (vector + rerank + structured calendar lookup), caching
analytics.py               anonymous usage events -> Supabase (supabase/analytics.sql)
pipeline/ run_pipeline.py  knowledge-base ingestion (see pipeline/README.md)
eval_retrieval.py          retrieval regression checks (python eval_retrieval.py)
.github/workflows/         daily calendar refresh, keep-warm ping
```

## Run it locally
```bash
npm ci && BACKEND_URL=http://localhost:8765 npm run dev          # frontend on :3000
pip install -r requirements.txt && uvicorn query_server:app --port 8765   # backend (needs .env)
```
`.env` needs `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (the **service / secret** key, never the anon key),
`GROQ_API_KEY`; optionally `METRICS_TOKEN` and `NUEVAI_CALENDAR_URL`. `GET /health` reports `data_ok`; if it is
`false` the backend cannot read the knowledge base and chat answers with an explicit error.

## Hosting
- **Frontend:** Vercel (`BACKEND_URL` or `CHAT_API_URL` points at the backend).
- **Backend:** Render free plan (512 MB RAM). No ML runs on it: questions and documents are embedded by the `embed` Supabase
  Edge Function (gte-small, 384-d; source in `supabase/functions/embed`), and results are re-ranked with a cheap lexical
  score, so the server needs ~100 MB. Chunks live in `document_chunks_v2`; set `SUPABASE_SERVICE_ROLE_KEY` to the
  *secret* key (the Edge Function accepts only callers holding it).
- A keep-warm GitHub job pings the backend every 10 minutes so the free plan does not sleep.

## Operations
Automations (all free: GitHub Actions + the existing Supabase/Render/Groq free plans). Secrets used are only the existing
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `NUEVAI_CALENDAR_URL`; none are ever echoed.

| Automation | When | What it does | Rerun it |
|---|---|---|---|
| `ci.yml` | every PR and push to main / `auto/**` | compiles all Python, runs `eval_retrieval.py` (read-only against the live index; skipped on fork PRs without secrets), `test_pipeline_fixes.py`, `test_resilience.py`, `test_email_ingest.py`, `test_lunch.py`, then `tsc --noEmit` and `next build` | locally: `python eval_retrieval.py`, `npx tsc --noEmit && npx next build`; on GitHub: Actions > CI > Re-run |
| `nightly-quality.yml` | daily 10:15 UTC | wakes the backend, runs `eval_golden.py` serially (6 s between questions, uses Groq quota, about 30-40 min), uploads the report as the `golden-report` artifact, then `scripts/check_baseline.py` fails the run (GitHub emails the owner) if the score is below `eval_baseline.json` score minus tolerance (now 0.872 - 0.05), or if fewer than 60% of questions could be graded (rate limits) | Actions > Nightly quality > Run workflow; locally `python eval_golden.py <backend-url>` then `python scripts/check_baseline.py --report data/eval/golden_*.json` |
| `refresh-knowledge-base.yml` | daily 13:30 UTC | iCal + cafe lunch menus + live team sheets -> chunk -> index. Around it: `scripts/index_guard.py before/after` fails the job if the chunk table loses more than 10% of its rows (a bad scrape), then `scripts/audit_data.py` re-checks the index against its sources (known conflicts live in `scripts/audit_known.json`). The workbooks and documents are refreshed by hand, so in CI the audit covers the calendar feed, live team sheets and database checks; run it locally for the full 27 groups | Actions > Refresh calendar > Run workflow; locally `python run_pipeline.py --stages calendar,lunch,live,chunk,index` and `python scripts/audit_data.py` |
| `keep-warm.yml` | every 10 min | pings `/health` so Render's free plan stays awake | - |
| `scripts/weekly_digest.py` | by hand, weekly | prints a markdown digest (visitors, top questions, failures, feedback; grader traffic excluded, emails/numbers masked); read-only, sends nothing | `python scripts/weekly_digest.py --days 7` (needs `.env`) and paste the output into a chat |

### Insights: product analytics and operations dashboard (`/insights`, `analytics.py`, `insights.py`)
What is tracked (anonymous only: a random browser id and a session id; no accounts, no IPs, no names, no stored personal data):
- **Browser events** (`lib/analytics.ts`, via `/api/track`): page/tab/feature clicks, `chat_sent` (with `via`), `chat_rendered` (time to first words and full answer as the visitor saw it), web vitals, client errors, and `session_start` with `visit_number`, `first_visit`, `days_since_first`. Every batch carries an envelope (`flags`: `i` internal, `wd` webdriver, `dev` localhost) and each event a client `eid`, so retries and double flushes are ignored.
- **Server events** (`query_server.py`, one `chat_answered` per question): `route` (cache_l1, cache_l2, canned, guard, structured, llm, degraded), final `model`, `fb`/`fb_why` (fallback and why), `attempts` (each model tried: status, error kind rate_limit/overloaded/timeout/connection/server_error/other, latency, retry-after), `retries`, `pt`/`ct`/`tok_src` (provider token usage, or a labelled estimate), stage timings (embed/vector/calendar/rerank/llm/total), `inflight`, `rss_mb`, `cold`, `up_s`, `topic`, `alen`. Plus `server_boot`, `rate_limited{limiter}`, `dependency_error{dep}`. All of it fails silent and adds no network round-trip to the answer path.
- Events stamped before this version have none of the server fields; the dashboard says so ("Data notes") and never back-fills them.

Audiences (`audience.py`): every event is stamped `user | internal | test | bot` at ingestion (`props._aud`, reserved `_` keys, no schema change), and every browser is re-classified across its whole history when the dashboard is built: your override (table `analytics_audience_overrides`) > test > internal > bot > user. `test` = ids starting `test-`, `golden-`, `redteam-`, `eval-`, `bench-`..., localhost/dev builds, or header `x-nuevai-test`; harness scripts now send `test-<harness>-<id>` and that header. `internal` = a browser marked as yours. `bot` = webdriver/headless or a UA such as python-requests, curl, Go-http, crawlers, link-preview fetchers, "Google" (a bare `node` UA is the Next proxy default and is NOT treated as a bot). Page-only visits are "no interaction", not bots. Pre-existing rows are classified by the same rules and labelled `inferred`.

Mark yourself: open `/insights` with a valid token (it sets a localStorage flag; undo on the Audience tab), or visit `/?internal=1` on another device (`?internal=0` undoes it). Already-recorded history: Insights > Audience > "Heaviest browsers" > real / me / test / bot (adds an override row; nothing is edited or deleted).

Dashboard: sticky header (token, range 24h/7d/30d/90d/All, audience selector, Pacific-time note, refresh, last updated, data-health strip); tabs Overview, Adoption, Usage, Engagement, Questions, Quality, System, Recent, Audience, Data notes. The state is kept in the URL (`?range=7&aud=users&tab=system`). Default audience is real users.

Aggregation (trade-off): the server loads one bounded, projected window (`ts,anon_id,session_id,event,props,ua`, newest first, paged by 1000, capped at `INSIGHTS_MAX_ROWS` = 60,000 with a visible `truncated` flag) for the range plus the previous equal period, and aggregates in Python with Pacific-time (DST aware) bucketing; results are cached 60 s. New-vs-returning, retention and funnels use an all-time per-browser profile from the SQL function `analytics_anon_profile()` (first seen, Pacific active dates, ingestion audience stamps, UAs), so they stay true outside the window. This is simple and correct up to tens of thousands of events per range; if volume grows 10x, move the heavy sums into SQL functions (the output shape of `insights.build` is the contract). Everything is testable offline (`test_insights.py`).

Data health: buffer flushed every 5 s and on shutdown (FastAPI lifespan + atexit), counters for accepted/written/dropped/duplicate/rejected events, last flush time, flush errors and table status, shown in the header strip and the Data notes tab. Retention of events: all history is kept; nothing is purged (no deletion tooling exists on purpose). Privacy: no IPs, no accounts, anonymous ids only; questions are stored truncated to 300 characters as before.

### Forwarded school emails (`email-ingest.yml`)
The owner forwards a school email (info update, schedule change) to a dedicated Gmail inbox; every 30 minutes a GitHub Action reads the unread
mail over IMAP (`imap.gmail.com`, app password) and indexes it, so the chatbot can cite it ("per the school email of Oct 28 ..."). Free: GitHub
Actions minutes only, no domain, no paid service. Secrets (repo secrets, never printed): `EMAIL_INGEST_USER`, `EMAIL_INGEST_APP_PASSWORD`,
`EMAIL_ALLOWED_SENDERS` (comma-separated); without them the script prints "not configured" and exits 0.

- **Gatekeeping:** the forwarder's From address must be on the allowlist AND Gmail's `Authentication-Results` must show `dkim=pass` or `spf=pass`
  for that address's domain; otherwise the message is logged (`rejected_sender` / `rejected_auth`) and never indexed. Mail from the inbox's own address is ignored.
- **Cleaning and privacy:** the original sender name (staff only), subject and date are taken from the forwarded block (the forwarder's own note,
  quoted replies, signatures, footers and tracking links are dropped); non-staff email addresses and phone numbers are redacted (`@nuevaschool.org`
  addresses and the two main school lines stay); anything that looks like student-level data (rosters, grade/GPA tables, student IDs, parent contact
  blocks, discipline or medical content) is NOT indexed and logged `needs_review`; instruction-like lines are scrubbed. Attachments are ignored (counted in the log).
- **Indexing:** chunks `email-<content hash>-<n>` in `document_chunks_v2` with `source_type=email`, the sent date and the sender's domain only. A
  re-forward of the same text is a `duplicate`; changed text for the same message replaces its chunks. Nothing expires or is deleted by the pipeline
  (`run_pipeline.py --prune`, the superseded flagging, `index_guard.py` and the audit all skip `email-*` rows); retrieval gives newer emails a modest
  boost, labels them `[School email, sent Oct 28, 2026]`, and the prompt tells the model to prefer the newest source and say where it came from.
- **Add or remove an allowed sender:** GitHub > Settings > Secrets and variables > Actions > update `EMAIL_ALLOWED_SENDERS` (comma-separated addresses).
  Locally put the same three variables in `.env`.
- **Review:** `python scripts/email_list.py` (last 30 log rows: status, subject, date; `--status needs_review`, `--chunks` to list indexed emails).
  The table is `email_ingest_log` (no bodies, no full addresses). `python scripts/audit_data.py` has an `email.*` group that checks metadata and privacy.
- **Remove an email:** `python scripts/email_remove.py "<subject text>"` shows what it would delete (dry run is the default); add `--yes` to delete
  it. It only ever touches `email-*` rows.
- **Rerun:** Actions > Email ingest > Run workflow. Locally: `python scripts/email_ingest.py --dry-run` (reads the mailbox, writes nothing, marks nothing read) or
  `python scripts/email_ingest.py --eml saved.eml` (offline preview of a saved message; add `--show-subjects` to print the chunk text).
  Scheduled workflows run only from the default branch, and GitHub pauses them after 60 days without repository activity.

**End-to-end test (once, after merging to main):** (1) from an allowed address forward any real school email to the inbox (Gmail "Forward", inline, not as an
attachment is best; avoid student lists); (2) Actions > Email ingest > Run workflow (or wait up to 30 min), the log should end with `summary: indexed=1`;
(3) `python scripts/email_list.py` shows the subject with status `indexed` (a `needs_review` / `rejected_*` status explains itself in the reason column);
(4) ask the site a question about the email's facts (answers can lag up to 6 h through the answer cache for repeated questions, so use a new wording) and expect
"per the school email of <date>"; (5) `python scripts/email_remove.py "<subject>"` to preview the removal.

### Cafe lunch menu (`pipeline/lunch.py`, `lunch_lookup.py`, `GET /lunch`)
The cafe (Epicurean Group, <https://nuevaschoolcafe.com/#our-menu>) posts one PDF per campus per month: San Mateo ("San Mateo US and Faculty Menu", the
Upper School campus) and Hillsborough ("Lower and Middle School"; the separate PreK-K menu is not read). The daily `refresh-knowledge-base.yml` run has a
`lunch` stage: one GET of the cafe's public Google Calendar feed (the menu widget on the page; each month's PDF is attached to an event), one GET per
Drive file, then the PDF is parsed (pdfplumber, in `requirements-pipeline.txt`) into one record per campus per day: `lunch-<campus>-<YYYYMMDD>` with
`metadata.source=lunch`, `campus`, `event_date`, `month`, `closed`, `items` (the dishes exactly as listed, under the cafe's own section names), the PDF link,
and the usual freshness fields. Days where the PDF shows a picture instead of a menu (no school, professional day) are `closed` records. **No dietary tag is
ever inferred**: the PDF's key (vegetarian, vegan, gluten free, ...) is a row of icons that is not attached to dishes, so the assistant says so and sends
allergy questions to the cafe; every lunch answer ends with "The menu can change; check with the cafe for ingredients or allergens."

- **Answering:** `intent.parse_lunch` (lunch/menu/cafe/vegetarian... plus today, tomorrow, weekdays, "Oct 14", next week) and `rag.lunch_data` look the day record up
  directly (no vector search, no calendar block). The chat request has an optional `campus` (`"san_mateo"` | `"hillsborough"`, anything else is ignored); a campus
  named in the question ("Hillsborough lunch", "MS", "lower school", "San Mateo", "upper school", "US") wins over it; with neither, both campuses are shown, labelled.
  Weekends and closed days say there is no lunch service (reason from the school calendar data when it has one); a month with no rows says "the <Month> menu isn't posted
  yet" and never shows another month. Lunch questions skip both answer caches (they depend on the campus and on today) and the campus is never logged or stored.
- **Endpoint:** `GET /lunch?date=YYYY-MM-DD&campus=san_mateo|hillsborough` (date defaults to today, Pacific) -> `{date, campus, items, note, available, month_available,
  month_label, source_url, closed_reason}`. No LLM, 5 min memory cache, 60 requests/min per client.
- **Monthly upkeep:** normally none: when the cafe posts the next month's menus the daily run picks them up (it looks at this month and next). If the cafe calendar has no PDF yet,
  the old rows stay and the app says the next month "isn't posted yet". **Manual drop** (when the cafe emails a PDF or the calendar is wrong): save each PDF as
  `data/pipeline/manual_docs/lunch/san_mateo_2026-11.pdf` and `.../hillsborough_2026-11.pdf` (`<campus>_<YYYY>-<MM>.pdf`; a drop wins over a download; a file whose own header names a
  different campus or month is refused), then `python run_pipeline.py --stages lunch,chunk,index --only-source lunch` (add `--no-lunch-download` to skip the network; `--dry-run` first).
  Past months are never deleted automatically; `--prune` removes a campus's old months only when that run produced new menus for the same campus.
- **Checks:** `python test_lunch.py` (offline), the `lunch` cases in `eval_retrieval.py` / `eval_golden.json` (they name October 2026 facts: when that month is pruned the retrieval cases
  skip themselves; refresh the golden facts from the new PDFs), and the `lunch.*` groups of `scripts/audit_data.py` (every weekday of a posted month present, no duplicate dates, valid campus,
  freshness metadata, index equals the parsed PDF; "lunch served on a no-classes day" is listed as information only).

Baseline: `eval_baseline.json` records which number is measured (239/274 = 87.2%, prompt 01) and which was only an estimate
(~98%, prompt 02, not used). After a full nightly run with few errors, record the real score with
`python scripts/check_baseline.py --report <report.json> --write-baseline` and commit the file. A red nightly run means a
regression or an exhausted Groq quota (the message says which); open the `golden-report` artifact for the failing questions.

### Quality eval (`eval_quality.py`, `eval/quality_set.json`, QUALITY_AUDIT.md)
A deterministic accuracy harness built for the launch bar: >= 95% correct on verifiable questions, 0 hallucinated facts, < 3% unnecessary refusals.
`eval/quality_set.json` has 241 curated questions (persona, campus, phrasing, behavior, expected facts, forbidden strings, source); `verified` items (234)
were mechanically checked against the live index by `python eval/build_quality_set.py` (it rewrites the file; add or change questions there, never by hand).
Unverified items are listed in `eval/UNVERIFIED.md` and skipped unless `--all`.

```bash
python eval_quality.py --mode retrieval                      # FREE, ~2 min: is the expected fact in the context the model would see? (also grades the no-LLM lunch/canned answers)
python eval_quality.py --mode llm --sample 40 --seed 1       # real answers via Groq, same prompt as production (query_server._prepare), cached by prompt hash
python eval_quality.py --mode llm --category lunch --token-budget 40000
python eval_quality.py --mode llm --ids ath-05,gm-02         # specific questions
python eval_quality.py --mode llm --only-failed data/eval/quality_<ts>.json
python eval_quality.py --mode llm --model openai/gpt-oss-20b # alternate model: results are labelled NON-PRIMARY
python eval_quality.py --self-test                           # grader unit tests, no network
python eval/diagnose_retrieval.py data/eval/quality_<ts>.json   # for each retrieval miss: where the answer chunk was lost
```
Labels (one per answer): `correct`, `correct_overhedged` (right but hedges or redirects needlessly), `unnecessary_refusal` ("I don't have that" while the data covers it),
`partial`, `wrong`, `hallucinated` (a date, time, phone, email, URL, room, score, amount, number or name that appears in neither the retrieved context, the question/history nor the index).
Not graded and never counted as passes: `ungraded_quota`, `stale_dynamic` (a date-dependent question past its `valid_through`), `error`. Dynamic items (day type, lunch, "next game") are re-derived from the live index at run time.
Cost control: Groq's free tier is ~200k tokens/day per model and 8k/min, shared with the live site; one answer is ~3.1k tokens. Answers are cached in `data/eval/cache` keyed by sha256(system prompt + context + question + history + model),
so re-runs only pay for questions whose prompt changed (the date line is ignored for non-dynamic questions). `--token-budget` stops cleanly and the rest are reported as `ungraded_quota`; `--tpm` paces under the per-minute cap.
Output: a table by category / persona / phrasing / behavior, taxonomy counts, over-hedge, unnecessary-refusal and hallucination rates, every flagged hallucination token, launch-bar status, `data/eval/quality_<ts>.json`;
`--write-baseline` also writes the compact `eval/baselines/baseline_<date>_<mode>.json`. The exit code is 1 below the launch bar (`--no-fail` to disable). Test traffic is sent as `test-quality-<n>` so analytics files it under "test".

More: [pipeline/README.md](pipeline/README.md) (ingestion, metrics, environment variables).
