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
