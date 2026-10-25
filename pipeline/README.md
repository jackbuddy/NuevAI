# NuevAI knowledge-base pipeline

One command scrapes, chunks, embeds and indexes everything the chatbot knows.

```bash
pip install -r requirements-pipeline.txt
python run_pipeline.py --dry-run          # crawl + chunk, show what indexing would change
python run_pipeline.py                    # the real run (needs SUPABASE_* in .env)
```

| Stage    | What it does | Needs a human? |
|----------|--------------|----------------|
| `public` | Crawls www.nuevaschool.org from the HTML `/site-map` + link following. Obeys robots.txt (incl. wildcard rules) and its 5 s crawl delay. | No |
| `nexus`  | Imports pages from `../nexus-scraper`; `--refresh-nexus` re-scrapes with the saved session. Pages containing any term in `data/pipeline/private_terms.txt` are dropped. | Once, to sign in (SSO/2FA). Re-sign-in when the session expires; the run says so and still uses the last good scrape. |
| `live` | Re-reads public Google Sheets that change during the year: the US Boys Basketball 26-27 tabs (fetched by tab id) and the Course Offerings workbook (downloaded as .xlsx). Configured in `pipeline/config.py` (`LIVE_SHEETS`). | No |
| `manual` | Indexes files you download yourself into `data/pipeline/manual_docs/` (.docx .pdf .txt .md .html .csv). See `DOWNLOAD_THESE.md`. | You download them |
| `calendar` | Reads the iCal feed in `NUEVAI_CALENDAR_URL` into dated event chunks + per-day digests; expired events are removed automatically. | No |
| `google` | Exports every linked Google Doc / Sheet / Slides file (course catalog, handbooks, club lists), following links between docs 2 hops deep. | No, for "anyone with the link" files. Private files are listed in `needs_access.json`. |
| `chunk`  | Heading-aware ~1,100-char chunks that carry their section trail; boilerplate de-duplicated. Builds the **link registry** and one retrievable "link card" per booking / form / payment / calendar / document / portal link. | No |
| `index`  | Embeds only new or changed chunks (BAAI/bge-large-en-v1.5, same as the server) and upserts to `document_chunks`. | No |

Useful flags: `--stages chunk,index`, `--max-public-pages N`, `--prune`
(delete chunks this pipeline wrote for pages that disappeared), `--prune-legacy`
(delete rows from the old `parse_and_chunk.py` pipeline — only after checking
answers on the new data).

## Unattended runs

`.github/workflows/refresh-knowledge-base.yml` refreshes the calendar daily (calendar + chunk +
index) and `keep-warm.yml` pings the backend every 10 minutes. The public site is crawled once. Add repo secrets `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY`. Nexus is deliberately not in CI: the saved
`auth.json` is a login session and should not live in GitHub.

## Output (`data/pipeline/`, gitignored)

`pages/` raw extracted pages · `links.json` classified links · `chunks.json` ·
`needs_access.json` docs we could not read · `report.json` per-stage stats.


## The app (frontend + backend)

* `query_server.py` (FastAPI, Render): `/api/chat/stream` (SSE), `/api/chat`, `/api/calendar`, `/api/links`,
  `/api/track`, `/api/metrics`, `/health`. Retrieval = vector search + cross-encoder rerank + structured calendar
  lookup (`intent.py`, `rag.py`). Answers and embeddings are cached (`cache.py`).
* Next.js app (`app/`, `components/`, `lib/`, Vercel): Chat, Calendar, Resources, and `/insights`.
* Regression check: `python eval_retrieval.py`.

### Instrumentation
Every browser gets a random `anon_id` (localStorage) and a `session_id` (30-minute rotation); both are attached to
every event, so each record is tied to the user who triggered it. The client batches events to `/api/track`, the
server stores them in Supabase table `analytics_events` (run `supabase/analytics.sql` once). The chat server also
records each answer with its timings. View everything at `/insights` with the `METRICS_TOKEN` set on the backend.

### Environment variables
| Where | Name | Purpose |
|---|---|---|
| Render | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GROQ_API_KEY` | data + LLM |
| Render | `METRICS_TOKEN` | enables `/api/metrics` and the `/insights` page |
| Render | `LLM_MODEL` (optional) | override the default `openai/gpt-oss-120b` |
| Vercel | `CHAT_API_URL` or `BACKEND_URL` | backend address (default `https://nuevai-backend.onrender.com`) |
| GitHub | `NUEVAI_CALENDAR_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | daily calendar refresh |


### Curated facts and planning workbooks
* `knowledge/*.md`: facts confirmed by people (bell times, what A'/B' mean, campus addresses). Each `## heading` becomes one searchable record.
  Edit the file, then run `python run_pipeline.py --stages chunk,index` (the daily GitHub job also includes them).
* Planning workbooks (`The Sheet`, clubs catalog; downloaded as .xlsx into `data/pipeline/manual_docs/`) are parsed by `pipeline/workbooks.py` into
  A/B day records, teacher -> course -> block -> room records, advisor rooms and one record per club. Student-name tabs are never read.
  Previous-year titles are dropped (`SUPERSEDED_TITLE` in `config.py`).
* After the Spring 2027 offerings are filled in: `python run_pipeline.py --stages live,chunk,index --prune`.

### Audit and freshness
* `python scripts/audit_data.py` re-derives A/B days, bell times, teacher-course-block-room records, clubs, team schedules and the iCal feed straight from the source files and compares them with the index (read-only). Exit 1 on a mismatch; known source conflicts are listed in `scripts/audit_known.json`. `--offline` skips network and database.
* Every chunk carries `indexed_at`, `source_date`, `school_year` and `stale` (see `freshness.py`). `stale` marks documents written for an earlier school year; retrieval demotes them and the prompt tags them OUTDATED. Existing rows get the new metadata by a metadata-only patch on the next index run (no re-embedding). `--keep-expired` skips deleting expired calendar rows.
* Rows the pipeline no longer produces from the workbooks are flagged `metadata.superseded` (ignored by retrieval) instead of deleted.

### Alpha feedback
Testers use the **Feedback** button (written notes) and the thumbs on answers. Read both at `/insights` (needs `METRICS_TOKEN`).
