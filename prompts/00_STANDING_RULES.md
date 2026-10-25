# Standing rules (prepended to every queued prompt)

You are improving NuevAI unattended. Nobody will answer questions while you work. Read HANDOFF.txt first for architecture, IDs and gotchas; trust the repo and live system over it if they differ.

## Autonomy
- Never stop to ask. Pick the most conservative sensible default, note it in your report, and continue.
- If something needs the user (a secret, a paid plan, a DB DELETE the permission system denies, a manual download), append a dated line to `NEEDS_USER.md` with the exact command or step, then move on to other work.

## Hard limits
- Free plans only (Render free, Supabase free, Groq free). Never add anything paid.
- Never enter, print, or commit secrets. `.env` stays gitignored. Never put the iCal feed URL in code.
- Never index student-level data (rosters, advisee names, student/parent emails). Do not weaken any privacy filter.
- Do not delete database rows or drop tables. Write the SQL to NEEDS_USER.md instead.
- Do not redesign the UI (white-to-navy, Manrope, light only). Polish only.
- Never use `open(p,"w").write(open(p).read())`. Keep work out of ~/Desktop.

## Git
- Work on branch `auto/queue` (create from main if missing; if it exists, continue on it). One commit per prompt, message `auto: <prompt name> - <one line>`, ending with the Co-Authored-By trailer required by the harness.
- Do NOT push to main and do NOT merge. Push the branch only if `gh` is logged in; otherwise leave it local.

## Gates (all must pass before you commit; if one fails, fix it or revert your change)
1. `python eval_retrieval.py` -> all cases pass (add cases for anything you fix; never delete or loosen a case to make it pass).
2. `python -m py_compile` on every changed .py file.
3. If frontend files changed: `npx tsc --noEmit` and `npx next build`.
4. If you touched query_server.py, rag.py or intent.py: start the local backend (`ANALYTICS_DISABLED=1 uvicorn query_server:app --port 8765`) and confirm /health data_ok, then run `python -u eval_answers.py http://localhost:8765` and read a sample of answers yourself.
Local env: create a venv in ~/Developer/.venvs/nuevai if none works (requirements.txt + requirements-pipeline.txt), load `.env`. Keep Groq usage low: sleep between LLM calls, cap bursts, stay under ~10 concurrent.

## Report
End with a short report appended to `QUEUE_LOG.md`: prompt name, what changed, measured before/after numbers, anything skipped and why, follow-ups. Be honest about failures.
