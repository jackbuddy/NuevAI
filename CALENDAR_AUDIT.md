# Calendar audit (2026-11-12)

Scope: every calendar-like data type the app or the bot uses, where its authoritative source lives, whether it is ingested, whether the app uses it,
how it stays fresh, and what is missing. Everything below was checked against the real feed (921 events), the real master-schedule workbook
(`The Sheet - Master Schedule 2026-27.xlsx`), the live team sheets and the live index (read-only); "fixed" means fixed in this branch and covered by
`test_calendar.py` / `scripts/calendar-logic.test.mjs`.

## 1. The daily "Refresh calendar" Action (why it failed, what changed)

| Step | What it did | Root cause | Fix |
|---|---|---|---|
| `Audit data against sources` | exit 1 every day since the audit was added | the audit was written for the owner's machine. In CI it compared the table with a run that has no local workbooks, pages or chunks: (a) `index.orphan_rows` reported ~805 "orphans" (nexus, google_doc, school_courses...) that were simply not produced by that run; (b) `teams.*` found real problems: basketball rows removed or changed on the sheet were never deleted from the index (only `calendar`/`calendar_day` rows expired), and games listed twice on the JV sheet (once with no time, once 16:00) were deduped to the copy WITHOUT the time; (c) `fresh.prior_year_flagged`/`same_document_two_years` re-derived staleness from chunk text, which loses a leading "Last updated: 5/09/2023" line | `audit_data.py --ci` (only bell/teams/ical/fresh/email/lunch, notes for the skipped groups, only a "mismatch" or crash fails, "conflict"/"orphan" only warn, an unreadable index or a missing calendar secret fails); the index stage now removes `calendar_sheet` rows dropped from a sheet read in that run; duplicate sheet rows keep the copy with a time (the other is info in the audit); the staleness check is one-sided when the saved page is not on the machine |
| `Upload report` | `Failed to CreateArtifact ... Unexpected token '<'` (10 minutes of retries, 44 min run) | GitHub's artifact service returned an HTML error page: transient, GitHub side | `continue-on-error`, 6 minute step timeout, a second attempt step after a 60 s wait, `retention-days: 14`, `if-no-files-found: warn`; the upload can never fail the job |
| warning | `Node.js 20 is deprecated` | checkout@v4, setup-python@v5, upload-artifact@v4 | checkout v7, setup-python v7, upload-artifact v7, setup-node v7 (release notes read: ESM migration, new optional inputs, `pip-install` input removed from setup-python (unused here); no input we use changed) in refresh, nightly, ci. `email-ingest.yml` is left for the lead. `keep-warm.yml` uses no actions |
| (latent) | the job's index stage deleted all "expired" calendar rows including this school year's history (the 14-day window of the deployed code) | `expired_calendar` = every calendar row not in this run's output | `plan_calendar_removals` (pipeline/index.py): removes only cancelled/moved FUTURE events, edited duplicates, rows before the school year, and dropped team-sheet games; past events of the current school year are kept as history; a plan whose feed-dependent removals exceed 25% of the calendar rows (truncated feed) is refused and reported (last year's rows go by date alone) |
| (latent) | `ci.yml` has never run on GitHub and would fail: `query_server` builds its Groq client at import and CI has no `GROQ_API_KEY` | | dummy key in the job env (the offline tests never call Groq); also runs test_flagged/test_data_coverage/test_quality_fixes/test_calendar and the node logic tests |

The job now fails only for: the pipeline (any stage; the report is still written with the failed stage), the index-size guard (>10% shrink), a real audit
failure, or a missing calendar secret. It writes a health summary (`scripts/ci_summary.py`) to the run page.

## 2. Coverage matrix: data type by data type

"Fresh" = how it gets updated. CI = the daily GitHub job (13:30 UTC). Manual = the owner exports/downloads and runs `python run_pipeline.py --stages ...` locally.

| Data type | Authoritative source | Ingested | Used by app/bot | Fresh | Gap / status |
|---|---|---|---|---|---|
| Calendar events (all divisions) | private iCal feed (`NUEVAI_CALENDAR_URL`) | yes: `calendar` rows (one per event, audience duplicates merged), `calendar_day` digests | Calendar tab (`/api/calendar`), Happening cards, chat structured lookup | CI daily (up to 24 h lag) | window: school-year start .. +430 days. Fixed: category quality, division tags, "(MOVED TO ...)" placeholders dropped, TZID/DST/RRULE handling (feed has 0 RRULE today; expansion added as a safety net) |
| Day types A / B / A' / B' for every date | master schedule tab "Calendar AB + Special Schedule" | yes: `school_day`, 215 dates Aug 19 2026 - Jun 2027 (148 class days: A 44, B 44, A' 30, B' 30; matches the sheet's own count table) | chat: yes (day digest and "is Oct 5 an A day"); Calendar tab: NOT before this branch, now badge on every date, plus the headline in the day panel | Manual (workbook export) | the only gap is automation: the workbook has tabs with student names, so it cannot simply be linked (see NEEDS_USER) |
| Bell schedule by day type | `knowledge/bell_schedule.md` + master schedule | yes, per-day times in each `school_day` record | chat; Calendar tab day panel (block order) | Manual / in git | Fixed: Community (40'), Community (90') and full-day days used the REGULAR times with the note "special events can adjust the regular times". The master sheet's "Special Schedule Configurations" cells are now parsed and written into those 32 dates (and as a knowledge fact) |
| No school / PD / faculty work days / breaks | feed ("No School", "No Classes") + master schedule "No Classes" labels | yes (both) | chat; Calendar tab (Off badge and tint, new); Happening "No school today" | CI (feed), Manual (sheet) | `audit ical.vs_master_sheet` cross-checks feed vs sheet (98 checks pass). Fixed: "No Classes: Faculty Work Day", "No Classes:Professional Learning Day", "Campus and Offices Closed" were `other`; now `no_school` |
| Early dismissal | master schedule ("Noon Dismissal", last day) | yes (`short_day`) | chat | Manual | only the last day of school is listed; no other early-release days exist in any source |
| Parent-teacher conferences (per division) | feed + master "Parent Teacher Conferences" days | yes | chat, Calendar | CI | Fixed: "No LS/MS Classes", "No US Classes", "No LS Classes" are now `no_school` with the divisions that are off; a San Mateo viewer is no longer told "No school today" for an LS/MS-only closure |
| Special schedules / assemblies / Community time | master schedule (day labels, "Special Schedule Days" table, configuration cells) | NOW yes: schedule key, event name (27 of 32 days; the 5 not tied to a date are listed below) | chat (record text), Calendar day panel + purple badge | Manual | 5 special days have no event row on the sheet: 2027-05-05, 05-07, 06-01, 06-02, 06-03 |
| Advisory meeting times by grade | master tab "Advisory Calendar" | yes, inside each `school_day` record | chat | Manual | not shown on the Calendar tab (chat only) |
| Block rotation / "what blocks meet" | master schedule | yes | chat; Calendar day panel ("B' day, blocks 5, 6, 8, 7") | Manual | fine |
| Course schedules by block (teacher, room) | master "Fall Roles"/"Fall Rooms", Course Offerings workbook | yes (`school_courses`) | chat | Manual; offerings workbook also via local `live` stage (CI runs `LIVE_KINDS=team_schedule` only) | known unresolved source conflicts (audit_known.json): Rooms vs Roles tab, offerings vs Roles. Spring 2027 not filled in yet |
| Club meetings | Clubs Catalog workbook | yes (`school_clubs`: day, time, room) | chat | Manual | weekly meetings are not calendar events, so they are not on the Calendar tab |
| Athletics games | feed (MS teams) + live sheets (US) | yes (`calendar`, `calendar_sheet`) | Calendar tab, Happening, "Follow team" | CI for the two US boys basketball tabs; the US girls basketball / volleyball sheets come from the `google` stage (Manual) | US fall/spring sports (soccer, tennis, golf, swim, cross-country, track, flag football, ultimate) have no schedule source at all. Fixed: stale games removed, JV duplicates deduped |
| Lunch | cafe PDFs (campus calendars) | yes (`lunch-<campus>-<date>`) | Happening card, `/lunch`, chat | CI daily | December menus are not posted yet (the app says so) |
| Back-to-School Night | feed events + hidden tab "BTSN Advisory Locations" | yes | chat | Manual | past (August) |
| Finals / exam / semester schedules | NOT FOUND in any source | no | no | n/a | the master sheet has no finals tab, the feed carries only SAT/ACT/ERB/PSAT. Owner must say where they live |
| Year-at-a-Glance | Google Doc "2026-2027 Year-at-a-Glance (last updated 10/5/26)" | yes (13 chunks, google_doc) | chat | Manual (`google` stage) | cross-checked against the master sheet by the audit (58 checks); not refreshed by CI |
| Monthly class-meeting counts | master sheet counts table (L3:T17) | no | no | n/a | not needed |

Master schedule, fully used? **No, it was used for day types, advisory, no-class labels, rooms and clubs but not for what it says about bells.**
Before: the 32 special-schedule dates (15 Community 90', 11 Community 40', 6 full-day) answered with the standard bell times and a hedge; the day
type never reached the Calendar tab; the "Special Schedule Days" event names (assemblies, Super Advisory, Diwali, CSL Day, Quest Day, trips fair)
never reached users. Now wired: special bell times with the day's own block order, the event name and note, the day type badge, block order,
no-classes tint, and the same facts as one knowledge record. Still unused: the "Master Calendar" tab (hidden duplicate of the first tab; the audit
compares them: 2 accepted conflicts, Oct 13 name and Memorial Day), the class-count table, Spring Roles/Rooms (hidden, empty until spring).

## 3. iCal edge cases

| Case | Behaviour | Test |
|---|---|---|
| all-day, exclusive DTEND | last day = DTEND - 1; "through Friday, ..." text; shown on each day (digests capped at 14 days) | yes |
| all-day start with a timed DTEND (Daylight Saving Time Ends) | read as ending that day (5 such events in the feed, noted by the audit) | existing |
| timed, crossing midnight | ends next day, only the start time is shown | yes |
| timezone | `Z` and any `TZID` are converted to Pacific (before: a non-Pacific TZID was read as Pacific); floating times = Pacific; unknown zone names keep the wall time; the feed today is 100% America/Los_Angeles | yes |
| DST | 20:00Z on Oct 31 = 1 pm PDT, on Nov 1 = noon PST; recurring events keep their wall-clock time across the change | yes |
| `VALUE=DATE-TIME` | was misread as all-day (prefix match on `VALUE=DATE`) | yes, fixed |
| recurring (RRULE) | expanded: DAILY/WEEKLY(BYDAY)/MONTHLY(day or nth weekday)/YEARLY with INTERVAL, COUNT, UNTIL, EXDATE and RECURRENCE-ID overrides; anything else keeps the first instance and is reported. The feed has none today (Google already expands them) | yes |
| cancelled (STATUS) | dropped; a cancelled override removes that instance | yes |
| moved/edited | a new content-derived chunk id; the old row is removed by the index stage if it is in the future (or the same title on the same day, or running today) | yes |
| title placeholders "(MOVED TO OCT 8) ..." | dropped (3 rows today); "[Moved from Nov 3]" (the new date) is kept | yes |
| audience duplicates | one row per (title, start, end, location); 249 merged | existing |
| passcodes in invite links | stripped from location and details | existing + audit fixed to compare the scrubbed form |
| window | school-year start (Aug 1) .. +430 days. Past events stay answerable ("That already happened on ...": system prompt) | yes |
| rollover | June: whole year kept. Aug 1: floor moves, last year's rows are removed by the next run. See section 5 for what does not roll over by itself |

## 4. Calendar UI and API

- `/api/calendar` equals the index: the Nov, Dec-Jan and Mar windows were compared event by event against the table (multiset equal; 143, 187 and 54 events). Fixed: rows were returned unsorted and
  truncated at one page (now ordered and paged); the response now has `days` (master-schedule record per date) and `division`/`no_classes` per event.
- Timezone: "today", Happening and day cells use America/Los_Angeles (existing); the .ics file declares `America/Los_Angeles` with VTIMEZONE rules.
- Fixed `.ics`: a timed event ending on another day kept the start day; a missing end time wrote a zero-length DTEND; backslashes were not escaped;
  long lines were not folded; trailing CRLF added.
- Category quality (every distinct non-athletics title was read): `other` -> `no_school` for "No Classes: ..." and "Campus and Offices Closed" (5 rows), conferences
  per division (7 rows), US Winter Formal/Coffeehouse/Spirit Days -> community, Evening of the Arts -> arts, "Classes Resume" -> academics.
- Division: new `division` (us/ms/ls; empty = whole school) read from the title ("MS", "LS/MS", "US", grade numbers, "Class of 2027", "HB"); 332 of
  the feed's events are MS-only. The Calendar tab hides events for the other campus by default when a campus is saved in Preferences (one checkbox
  shows them; the count is displayed) and Happening applies the same rule. No campus saved = everything, as before.
- Month view: A/B/A'/B' badge on each date (purple when the day runs on a Community-time schedule), "Off" tint on no-classes days, legend line, the
  day panel headline ("B' day, blocks 5, 6, 8, 7, Community time (90 min) schedule") and the event name. Hillsborough viewers see no US badges.
- Happening: "No school today" now respects the viewer's division; programs respect the campus.

## 5. Gaps that need the owner (also in NEEDS_USER.md)

1. Finals/exam week schedules: where do they live? (no source today)
2. Master schedule automation: the workbook has student-name tabs, so it must not be shared as is. Copy only "Calendar AB + Special Schedule" and
   "Advisory Calendar" into a new Google Sheet shared "anyone with the link" and send the link; the daily job could then read the day types live.
3. US athletics beyond basketball: a Google Calendar (or the team sheets) for each US sport, or the athletics department's calendar link.
4. Optional: separate Google Calendars for clubs and for MS/LS divisions, only if they hold events missing from the main feed (the main feed already
   carries 332 MS and 17 LS events).
5. August rollover (before Aug 1 2027): export the 2027-28 master schedule and clubs workbooks, update `SCHOOL_YEAR_START` is automatic (Aug 1) but the
   stored `stale` flags of non-CI documents are only recomputed by a local `--stages chunk,index`; nothing rolls over by itself for the workbooks.
6. The 2026-27 special-schedule dates 2027-05-05, 05-07, 06-01..06-03 have no event name on the sheet.
