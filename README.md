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

### Auto-ingestion from nuevai.updates@gmail.com (emails + shared Google Sheets/Docs)
Anything sent to the account's inbox (the email section above) or **shared with the account in Google Drive** (a Sheet or Doc, or a folder of them) flows into the
knowledge base on the existing 30-minute workflow `email-ingest.yml`: step 1 reads new mail, step 2 (`python scripts/email_ingest.py --only google`) reads Drive.
Each run only does what is new: mail by IMAP unseen state; files by Drive id + `modifiedTime`/`version` against the manifest.

- **Architecture:** `pipeline/gapi.py` (OAuth refresh-token client over `requests`: Drive `files.list` with `sharedWithMe = true` plus recursion into shared folders,
  Sheets API v4 `spreadsheets.get` with grid data, Docs via Drive HTML export; backoff on 429/5xx, token refreshed in-process) -> `pipeline/gsheets.py` (cells, merges, hidden rows,
  stacked tables -> the same CSV-style table text as the existing Sheets path, row-aligned chunks, event records) / `pipeline/share_ingest.py` (Docs, manifest, replace, run loop) ->
  `pipeline/schedule_text.py` (dates/times/practice summaries, shared with emails). Email extensions are in `pipeline/email_ingest.py`.
- **Manifest (survives CI runs):** Supabase table `share_ingest_manifest` (one row per file, keyed by a 12-hex hash of the Drive file id: modifiedTime, version, status, retry counter,
  missing-run counter) and `share_ingest_log` (review log). Created with `supabase/share_ingest.sql` (already applied). If the tables are missing the run still works from the chunks' own
  metadata and logs to stdout. No bodies, file ids or owner addresses are stored (owner = email domain only).
- **IDs and replace:** every chunk of a file is `share-<file hash>-...` (text `...-s<tab id>t<n>-<part>` / `...-d-<n>`, events `...-e<hash>`). An edited file upserts changed chunks, then deletes the ids
  of that file's prefix that are no longer produced (rows removed from the sheet disappear). Re-sharing the same file is a no-op. Deletion is limited to the exact `share-<hash>-` prefix.
  Emails: `email-<content hash>-<n>` as before; an UPDATED announcement (same normalized subject + same original sender = thread key, stored as a hash) flags the older version
  `superseded` (kept, ignored by retrieval); identical re-forwards stay no-ops; a late-arriving older version is the one flagged.
- **Chunking rule:** by whole rows / logical tables, never by size: a table stays in one chunk up to ~1,500 characters, longer ones are cut BETWEEN rows with the header row repeated in each
  chunk; an over-long single row gets a chunk of its own; stacked tables, side-by-side tables, captions and repeated header rows are recognised; hidden rows/columns are left out and noted.
- **Events:** schedule tables (HOME TEAM/AWAY TEAM like the school's sheets, or OPPONENT + H/A + LOCATION) and email/doc game lists also produce `calendar_sheet` event records (same schema as
  `pipeline/sheets.event_chunk`: `kind` game/practice/event, `event_date`, `home_away`, team label like `US Boys Soccer Varsity`) so "next game", the Calendar tab and Happening cards see them. Recurring
  practice ("Tue/Thu 4:00-5:30 PM") is kept as a clean summary line on top of the text; dated practice events are only created when the announcement bounds them ("through Dec 12").
- **Precedence when the same game is in several places** (public team tab `sheet-*`, shared sheet `share-*`, email `email-*`): same team words + date + kind + opponent = one event; the NEWEST source wins by day
  (shared sheet: Drive last-modified; email: sent day; public tab: day last downloaded); a tie goes to the shared sheet/email. Resolved at read time in `event_precedence.py`; no other feature's rows are touched.
- **Labels/prompt:** context shows `[Shared sheet: <title>, updated Nov 10, 2026]` / `[Shared doc: ...]` / `[School email, sent ...]`; newest wins; the model cites "per the varsity soccer schedule sheet updated Nov 10", never an owner or address.
- **Never indexed:** roster-like or student data (name columns with grade/position/size/contact, student/guardian columns, sign-up/response/attendance/roster file names, `guard.student_data_reason`) ->
  logged `needs_review`; a roster tab is skipped while the schedule tab of the same file is still indexed; sign-up/volunteer/parent columns are dropped; non-staff emails/phones redacted everywhere; hidden tabs are not read.
  Share rows are exempt from every prune/superseded/orphan routine (`freshness.is_share_row`, `is_ingested_row`).
- **Failures:** a bad file or message is logged with a reason and skipped; retried up to 3 times then `failed` until it changes. Exit code 1 only for systemic problems (auth/API disabled, Drive listing down, database/embedding down).
  Runs are time-boxed (`SHARE_INGEST_BUDGET_SECONDS`, 120 in the workflow): newest-changed files first, the rest wait for the next run. Caps: 25 tabs, 2,000 rows x 40 columns per tab, 150 chunks per file (truncation is logged).
  A file no longer listed for 2 complete runs in a row is flagged `superseded` (chunks kept) and the manifest says `unshared`; sharing it again restores it. An empty or incomplete listing never unshares anything.

**One-time Google setup (about 10 minutes):**
1. console.cloud.google.com -> create a project (any name). APIs & Services > Library: enable **Google Drive API** and **Google Sheets API**.
2. OAuth consent screen: user type **External**, app name, your email; scopes `.../auth/drive.readonly` and `.../auth/spreadsheets.readonly`; add `nuevai.updates@gmail.com` as a **Test user**.
3. Credentials > Create credentials > OAuth client ID > application type **Desktop app**. Copy the client id and secret.
4. **IMPORTANT, do this before step 5: set the consent screen's Publishing status to "In production" (Publish app). An External app left in "Testing" makes Google expire refresh tokens after 7 DAYS, and the Action turns red a week later.
   The app may stay unverified (you only see an "unverified app" warning once; personal-use limits apply).**
5. On your machine, signed in to the browser as nuevai.updates@gmail.com: `GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... python scripts/google_auth_setup.py` (or run it and answer the prompts).
   It prints only the refresh token, once, and writes nothing to disk.
6. GitHub > Settings > Secrets and variables > Actions: add `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`. Until they exist the step prints "Google ingestion not configured" and exits 0 (email keeps working).
If the token is ever revoked or expires, the run turns red with "Google refused the stored refresh token ... Re-authorize": repeat step 5 and update the secret.

**Review / remove / force:** `python scripts/share_list.py` (log; `--files` manifest; `--chunks` indexed files; `--status needs_review`), `python scripts/share_remove.py "<title>"` (dry run by default, `--yes` deletes that file's `share-` rows
only and marks it `removed` until it changes), `python scripts/email_ingest.py --only google --file-id <Drive id>` (re-ingest one file now), `--force`, `--dry-run`, `--limit N`, `--only email|google`;
`python scripts/email_ingest.py --sheet-json saved.json --show-subjects` analyses a saved spreadsheets.get response offline. `python scripts/audit_data.py` has `share.*` groups. Tests: `python test_share_ingest.py` (offline, fake Google/Supabase).

**Exact end-to-end test (after the secrets exist and this is merged to main):**
1. Share a game-schedule Google Sheet (viewer is enough) with nuevai.updates@gmail.com. Use a title like "Varsity Boys Soccer 2026-27" with a header row `DATE | OPPONENT | H/A | TIME | LOCATION` and a few future games; no student names.
2. From an allowed address, email (or forward) a practice announcement, e.g. subject "Ultimate Frisbee practice" and body "Practice: Tuesdays and Thursdays 4:00-5:30 PM at Field 2" plus a line `Sat 11/21 vs Menlo 10:00 AM (Home)`.
3. Actions > Email ingest > Run workflow (or wait <= 30 min). Both steps should end with `summary: indexed=1` style lines; `python scripts/share_list.py` shows the sheet `indexed` with chunks and events; `python scripts/email_list.py` shows the email.
4. Ask the site (new wording each time because of the answer cache): "when is the next varsity soccer game", "when does Ultimate Frisbee practice". Expect the dates/times/places from your files and "per the ... schedule sheet updated <date>" / "per the school email of <date>".
5. Update test: change a time and delete a game row in the sheet, rerun the workflow: `share_list.py` shows it `indexed` again ("N changed, M removed chunk(s)"); the bot gives the new time and no longer lists the deleted game; `share_list.py --chunks` still shows one entry for the file. Send a corrected version of the email (same subject, same sender): the old one is `superseded`.
6. Broken-input test: share a sheet named "Test roster" (or one with Student/Grade columns) and email gibberish: the run stays green, `share_list.py --status needs_review` explains the sheet, `email_list.py --status error` shows the bad mail (after 3 runs it is marked read).
7. Cleanup: `python scripts/share_remove.py "Varsity Boys Soccer"` (preview), then `--yes`.

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
`eval/quality_set.json` has 283 curated questions (persona, campus, phrasing, behavior, expected facts, forbidden strings, source); `verified` items (278)
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

### Answer quality fixes (qnorm.py, refusal.py, rag.py, query_server.py; QUALITY_AUDIT.md "Fix log")
- `qnorm.py` + `vocab_freq.json`: typo/slang repair and word rarity from the index vocabulary. Rebuild after big data changes: `python scripts/build_vocab.py` (read-only, ~6 requests). Without the file only the slang table applies.
- `refusal.py`: the one refusal/redirect detector, imported by the server (analytics, cache policy) and `eval_quality.py`. The prompt's canonical "not in my data" opening is **"I don't have that"** (`refusal.canonical_refusal`).
- Retrieval: keyword search (FTS) runs three ways (broad OR, rare words, rarest AND next word) with a retry; names/acronyms get protected slots; per-title caps and fuzzy copy dedupe; division (US/MS/LS) detection labels documents and ranks the asker's division first; context shows a query-centred window (<=1,300 chars of body) instead of the first 1,100 characters.
- Server: refusals/hedged answers are never cached; non-primary-model answers only 5 min in memory (never L2); empty completions are retried (bigger budget, then next model, then the degraded answer); credentials are scrubbed from context and answers (`guard.scrub_credentials`).
- `chat_answered` now carries `fallback` (shared detector), `refusal`, `canonical`, `redirect`, `hedged`, `rroute` (docs|calendar|entity|lunch), `div`, `chunks` [{id,t}] and `answer_head` (<=600 chars, emails/phones redacted); the SSE `done` event carries `route`, `model`, `limited`. Limited (refused/hedged) answers send no suggestions so the client keeps its own Browse Resources / What can I ask? chips; degraded answers send Try again / What can I ask? / calendar chips. Frontend TODO (not done here): record `via:'suggestion'` for chip clicks and read `done.limited`/`done.route`.
- Tests: `python test_quality_fixes.py` (offline, fake LLM, no quota). Retrieval gate: `python eval_quality.py --mode retrieval` (run 2-3 times; the ANN search moves 1-4 borderline items).

More: [pipeline/README.md](pipeline/README.md) (ingestion, metrics, environment variables).

### Canonical lists and data coverage (`knowledge/*.md`, `pipeline/canonical.py`, `pipeline/public_pages.py`, `scripts/flag_rows.py`)
Questions like "what sports does Nueva offer?", "who do I contact for X?", "how much is tuition?", "what clubs meet on Tuesdays?" are answered from enumerated, dated lists, not from whichever page the search happens to surface.
* **Curated, source-quoted lists** live in `knowledge/*.md` (`athletics_sports`, `leadership_and_contacts`, `admissions`, `academics`, `calendar_essentials`, `places_and_transport`; each `## heading` is one record). Each file starts with `Last updated: YYYY-MM-DD`; each section states its source, the date of that source and the school year, and ends with `<!-- verify: title~<page title> :: token ; token | url~<url part> :: token -->` naming the stored source page and the exact strings that must appear in it. `python scripts/check_facts.py` (and audit group `canon.verify`) re-proves every fact offline against `data/pipeline/pages`. Add a fact only with a source and a verify line; sections over ~1,150 characters are split at bullets automatically (records stay under the 1,600-character chunk limit).
* **Derived lists** are built on every `chunk` run by `pipeline/canonical.py` (source `school_canonical`, not hand typed): clubs directory by meeting day (from the Clubs Catalog workbook), departments and courses (from the 2026-27 Course Catalog), Upper School no-class days and breaks (from The Sheet), teams that have a schedule (from the calendar feed and team sheets), forms/booking/payment/portal/document link directories (from the link registry). They refresh whenever their sources do.
* **Public pages**: `python run_pipeline.py --stages public_pages` re-reads the 11 URLs in `config.PUBLIC_PAGES` (admissions, FAQ, tuition, transportation) with one polite GET each (project user agent, robots.txt, 5 s delay, 20 h HTML cache in `data/pipeline/public_html/`). The crawler now keeps collapsed FAQ/accordion answers with their question (they used to be dropped as hidden chrome). These pages get their own chunk ids (`id_seed` = url + `#public`), so the older capture of the same URL is never overwritten.
* **Calendar history**: `calendar_feed.window_floor()` keeps every event since August 1 of the current school year (the feed holds history from September), so "that already happened on <date>" is answerable. The daily refresh still removes rows that fall out of the window or were edited.
* **Never indexed**: `pipeline/safety.py` cuts shared logins, passwords, meeting passcodes, `?pwd=` URL parameters and personal cell numbers out of every page, calendar description and link before chunking (policy wording such as "never share your password" is untouched). Tested in `test_data_coverage.py`.
* **Additive indexing**: `python run_pipeline.py --stages chunk,index --additive-only --keep-expired --only-source school_facts,school_canonical,public_site --dry-run`, then without `--dry-run`. `--additive-only` inserts rows whose chunk_id is new and does nothing else (no rewrite of a changed row, no patch, no flag, no delete); always pair it with `--only-source` so a stale local file for another source cannot add rows. Plain runs (the daily CI job) behave as before.
* **Hygiene**: `python scripts/flag_rows.py` (dry run) / `--apply` flags rows `metadata.superseded=true` with `flag_reason` and `flagged_at` (credentials/personal numbers, lower-ranked duplicate copies of the handbooks and catalogs, earlier captures of refreshed public pages); retrieval ignores them and a freshness patch keeps the flag. `--sql` prints the review and delete SQL for the owner (nothing is executed). `python scripts/audit_data.py` groups `canon.*` check all of it.
* **Quality set**: the `cov-*` items in `eval/build_quality_set.py` cover this data and are verified against the original source chunks. The `absent` checks of no-such-thing items ignore the curated records (they state the negative on purpose).
