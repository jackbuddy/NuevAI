# Sports audit: Upper School athletics 2026-27 (read 2026-11-12)

Scope: every Upper School (US) team at the San Mateo campus: which teams exist, gender, varsity/JV, season, league, venue, contact, game schedules. Middle School is out of scope except where a source mixes it in. Everything below was read from the sources on 2026-11-12; nothing is from memory.

## 1. Source hierarchy and what was read

| Rank | Source | Read | Used for |
|---|---|---|---|
| 1 | nuevaschool.org/school-life/athletics ("Sports Offered" list, live page) | 2026-11-12 | the school's own US/MS team list by season |
| 1 | Upper School Athletics Handbook (Google Doc) | captured 2026-10-22 | leagues (PSAL/WBAL/CCS), venue table, contacts, JV/varsity definitions |
| 1 | Student & Family Handbook (athletics sections, "limited updates 1/1/2026") | captured 2026-10-22 | US/MS/LS team lists, leagues |
| 1 | "US P.E. requirement and PE information" (Google Doc) + Nexus post "PE Requirements for Graduation" (Jul 21, 2026) | 2026-10-22 / 2026-09-04 | interscholastic offerings 2026-27, PE classes |
| 1 | **Public athletics calendar** (nuevaschool.org/school-life/athletics/athletics-calendar, Finalsite calendar 375) | all months Aug 2026 - Jun 2027, 2026-11-12 | every US team's games, times, home/away through Feb 27, 2027 (this was missing from the index: the private whole-school iCal feed carries only Middle School games and a few US meetings) |
| 1 | Team schedule Google Sheets: US Boys Basketball (varsity, JV, practice), US Girls Basketball (season, practice), US Girls Volleyball (varsity, JV) | 2026-11-12 | games, notes (canceled/moved), JV volleyball, practice grids |
| 1 | School emails already in the index (One-Stop Shopping Oct 23 / Nov 6, basketball coach emails) | index | confirm girls' golf, cross-country senior night, basketball tryouts |
| 2 | League pages (PSAL / WBAL / CCS) | not fetched | only league names in the handbook were used; no league page was needed to resolve a conflict |
| 3 | MaxPreps (school page + 15 team schedule pages) | 2026-11-12 | supporting evidence only; labelled third-party |

Bot/robots notes: nuevaschool.org robots.txt allows the athletics pages (Crawl-delay 5, honoured); MaxPreps robots.txt does not disallow `/ca/<city>/<school>/<sport>/schedule/`; one GET per page, 6 s apart, project User-Agent "NuevAI-bot (school info assistant)"; no challenge or block appeared, nothing was circumvented. WebSearch was not needed.

## 2. Verified Upper School team matrix, 2026-27

Confidence: high = two or more official sources agree AND games are on an official schedule; medium = official lists agree, no schedule posted yet.

| Sport | Girls | Boys | Coed | Levels (official schedule) | Season | League | Home venue / practice | Confidence |
|---|---|---|---|---|---|---|---|---|
| Volleyball | yes | spring | | girls varsity + JV (JV sheet tab, calendar); boys varsity | Fall (girls) / Spring (boys) | PSAL | Nueva US Gym | high / medium |
| Cross Country | yes | yes | (called "Coed" in the handbook) | boys varsity + JV race; girls varsity | Fall | PSAL (Bay Division); CCS and CIF | Hallmark Park, Nueva Hillsborough campus (handbook); senior-night race at Crystal Springs XC course (email); practice various / Bay Meadows Park | high |
| Tennis | fall (varsity + JV) | spring | | girls varsity + JV on the calendar; boys none yet | Fall (girls) / Spring (boys) | WBAL (supplemental) + WBAL/CCS playoffs | girls Cañada College; boys Skyline College | high / medium |
| Golf | **fall** | spring | (athletics page: "Coed Golf" spring) | girls: 10 matches Sep 1-Nov 3 incl. CCS Team & Individual Championships; spring: none yet | Fall (girls) / Spring | WBAL (supplemental) | Crystal Springs Golf Course, practice Stanford Golf Course (handbook venue row says Boys Golf); girls' senior night at Baylands Golf Course (email) | high (girls fall) / medium (spring) |
| Basketball | yes (varsity) | yes (varsity + JV) | | boys V+JV, girls V on sheets and calendar | Winter | PSAL; CCS, NorCal, CIF | Nueva US Gym | high |
| Soccer | yes | yes | | varsity only on the calendar | Winter | PSAL; CCS | Skyline College; practice Skyline / Nueva MS campus | high |
| Swimming & Diving | yes | yes | (handbooks: "Coed") | none yet | Spring | WBAL (supplemental) | various Bay Area; practice Bay Club | medium |
| Track & Field | yes | yes | (handbooks: "Coed") | none yet | Spring | PSAL (athletics handbook) vs WBAL (Student & Family Handbook): unresolved | various; practice College of San Mateo, Skyline | medium |
| Beach Volleyball | yes | | | none yet | Spring | not stated | Notre Dame Belmont; practice Sports House | medium |

**Not confirmed as teams:** Boys' Flag Football (Fall) and Girls' and Boys' Ultimate Frisbee (Fall and Spring): only on the PE page's "interscholastic offerings" list.
**"Does NOT have" (confirmed absent by the school's own complete "Sports Offered" list and every other published list):** tackle football, hockey, baseball, softball, lacrosse, rugby, wrestling, water polo. (This was already in knowledge/athletics_sports.md; the live athletics page now backs it as a complete list.)
**Coaches:** only Robert Lopez (Head Coach, Cross Country and Track & Field) is named publicly as a team head coach; Brett McCabe (Director), Angie Purchio (athletic trainer) and Nick Hryekewicz (strength) are staff. No head coach is published for any other team.

## 3. Disagreements between sources and how they were resolved

| Topic | Sources and what they say | Resolution | Confidence |
|---|---|---|---|
| Girls golf (fall) | PE page: Girls' Golf (Fall). Athletics page and Athletics Handbook venue table: no fall golf. S&F Handbook: "Coed Golf" spring only. | Real: the official calendar lists 10 Girls Golf matches Sep 1-Nov 3 (incl. "CCS Team & Individual Championships") and a school email (Oct 23) mentions the girls' golf senior night at Baylands. The athletics page and handbook lists are incomplete. | high |
| Spring golf gender | PE page + Athletics Handbook venue table: Boys' Golf. Athletics page + S&F Handbook: Coed Golf. | **Unresolved**: a spring golf team exists; boys-only vs coed not decided by any source. | low |
| Boys' Flag Football, Ultimate Frisbee | Only on the PE page (interscholastic). Handbook and the Jul 21 Nexus post: ultimate frisbee is a before/after-school PE class; flag football is a US club and an MS team. No games on the athletics calendar Aug-Feb, absent from MaxPreps and the athletics page. | Not confirmed as teams; the bot says so and points to athletics@nuevaschool.org. | medium |
| Swimming & Diving, Track & Field | Athletics page and PE page: separate girls' and boys' teams. Handbooks: "Coed". | Girls' and boys' teams (one program); the handbooks' "Coed" is read as a combined program. | medium |
| Track & field league | Athletics Handbook: PSAL. S&F Handbook: WBAL. | Unresolved; Athletics Handbook used first (Upper School's own document). | low |
| Number of US sports | Athletics page says "10 different sports in three seasons" but lists nine. | Unexplained; noted. | n/a |
| MS Tennis wording | "Tennis" and "Coed Tennis" both in the S&F Handbook MS list. | Out of scope for US; the spring MS list is stated as written. Still ask athletics. | n/a |
| Lower School list | The knowledge file listed Coed Swim and Coed Golf for grade 4; the handbook's Lower School list has only Boys' Flag Football and Coed Cross Country in the fall. | Fixed in knowledge/athletics_sports.md (a real error I found and corrected). | high |
| Varsity boys basketball opener | Team sheet main table: Wed Nov 18; the sheet's repeated "home schedule" table and the athletics calendar page: Tue Nov 17 (7 PM scrimmage). | Team sheet main table used (it is the managed table: it also carries the 12/3 Design Tech home game and the 2/1 KIPP move the calendar page lacks); the record carries a note "athletics calendar lists November 17". Ask athletics to confirm. | medium |
| Time conflicts | JV girls volleyball vs Castilleja: sheet 4:00 PM, calendar 5:15 PM; boys varsity vs Eastside / KIPP / Kehillah and girls basketball vs Eastside: calendar times differ by 30-150 minutes from the sheet. | Sheet wins; the other time is kept as a note on the game record (5 such notes). | medium |
| Girls volleyball repeated table | The sheet's second "Varsity Home Schedule" table has a different time (5:30 vs 3:45 on 9/17 and 6:00 vs 5:30 on 10/8). | The first (main) table is used; the calendar page agrees with it for 9/17 varsity. | high |
| Boys basketball 2/1 vs 2/2, girls basketball 1/5 vs 1/4 | Calendar / MaxPreps are one day off from the sheet ("Game moved from 2/2 to 2/1" is in the sheet). | Sheet date wins; the other date is a note. | high |

## 4. MaxPreps (third-party): right, wrong, placeholder

- **Right:** Girls' and Boys' Cross-Country, Girls' Tennis, Girls' Volleyball, Boys' and Girls' Basketball (boys with JV), Boys' and Girls' Soccer, Boys' Tennis, Boys' Volleyball, Boys' Golf, Girls' and Boys' Swimming, Girls' and Boys' Track & Field; colors/mascot/address; league labels ("PSAL - Bay", "Private School - Bay", "PSAL - North") are third-party and not used as facts.
- **Placeholder / not a Nueva team:** Girls' Softball (spring 26-27, varsity), Boys' Basketball Freshman (24-25 only), Girls' Soccer JV, Boys' Tennis JV, Boys' Volleyball JV (no school source lists any of them).
- **Missing on MaxPreps:** Girls' Golf (fall), Girls' Beach Volleyball.
- **Data coverage:** only girls' volleyball, basketball (boys, girls) and soccer (boys, girls) pages carry games; cross-country, tennis, golf, swimming and track pages are empty; boys' volleyball has 2 spring games.
- **How it is used:** pipeline/sports.py ingests it only for the 17 catalog teams, only for games no official source lists (10 games) and for public results of past games (21 results, shown as "Result (MaxPreps (third-party))"), always labelled third-party with the read date. It never overrides the sheets or the calendar.

## 5. Schedule coverage per US team (index state after this work, read 2026-11-12)

| Team | Status | Source(s) | Games | Range | Last read |
|---|---|---|---|---|---|
| Girls Volleyball varsity | complete (season over; CCS ended Nov 7) | team sheet (+ calendar, MaxPreps results) | 21 games, 32 records | 2026-08-07 to 2026-11-07 | 2026-11-12 |
| Girls Volleyball JV | complete (season over) | JV tab of the volleyball sheet (+ calendar) | 14 | 2026-08-07 to 2026-10-22 | 2026-11-12 |
| Girls Tennis varsity / JV | complete (CCS ended ~Nov 18) | athletics calendar | 28 / 6 | 2026-09-01 to 2026-11-18 | 2026-11-12 |
| Girls Golf | complete (CCS Nov 3) | athletics calendar | 10 | 2026-09-01 to 2026-11-03 | 2026-11-12 |
| Cross Country (boys, girls) | meets only; State Nov 28 | athletics calendar | 8 + 1 JV-only meet | 2026-09-04 to 2026-11-28 | 2026-11-12 |
| Boys Basketball varsity | full season + playoffs | team sheet (+ calendar) | 26 + round entries | 2026-11-02 to 2027-03-12 | 2026-11-12 |
| Boys Basketball JV | full season | team sheet | 23 | 2026-11-02 to 2027-02-11 | 2026-11-12 |
| Girls Basketball | full season + CCS | team sheet (+ calendar) | 26 | 2026-11-02 to 2027-02-27 | 2026-11-12 |
| Boys Soccer | full season + CCS | athletics calendar (+ MaxPreps for 2 games) | 22 | 2026-11-17 to 2027-02-27 | 2026-11-12 |
| Girls Soccer | full season + CCS | athletics calendar (+ MaxPreps for 1 game) | 24 | 2026-12-01 to 2027-02-27 | 2026-11-12 |
| Boys Volleyball | third-party only, 2 games | MaxPreps (labelled) | 2 | 2027-04-07 to 2027-04-23 | 2026-11-12 |
| Boys Tennis, Golf (spring), Swimming & Diving (girls, boys), Track & Field (girls, boys), Girls Beach Volleyball | **no schedule yet** | none | 0 | | |

Totals now in the index: 265 team event records (153 team sheet, 102 athletics calendar, 10 third-party) in place of 125 before (which had no tennis, golf, cross-country or soccer). Results: 21 games carry a public result (third-party).

## 6. Data problems found and fixed

- US basketball rows listed twice / stale (audit: "times None and 16:00"): the sheets had changed since the index was built (CSD Fremont 11/17 -> 11/18, Design Tech 12/3 away -> home, KIPP 2/2 -> 2/1, girls basketball changes, the repeated "home schedule" tables); old rows had different chunk ids and were never flagged. Fix: rows the plan no longer produces are flagged superseded by scripts/flag_sports_rows.py (57 calendar_sheet rows, metadata only); the sheet parser now treats the second "home schedule" table as a fallback only, drops "Bye" rows ("Game Bye - Away" was being indexed as a game), marks canceled games (JV volleyball 9/15 and 10/13), and keeps the same sheet from arriving twice.
- US home games had no venue ("no game venues for US basketball"): home games now carry the Upper School Athletics Handbook venue (Nueva US Gym, Skyline College, Cañada College), labelled with its source.
- The girls basketball and volleyball sheets are now configured live tabs (config.LIVE_SHEETS), with the JV volleyball tab and the practice grids; rows containing email addresses (a side list of other schools' coaches) are dropped before anything is saved.
- A stale Nexus copy of the volleyball sheet (conflicting times) was flagged and is no longer generated.
- Lower School sport list corrected; knowledge headings rewritten so the corrected records are inserted additively (old records flagged).

## 7. Rosters and privacy decision

Rosters were **not** indexed, and no student name, student email or phone was stored: the owner's standing rule is never to index student-level data. Only roster size/structure would be recorded if a source stated it; none does. Coach and staff names with published business contact details (public staff page) are indexed. The sheets' "Roster", "Guest List", "Senior Night" and "Varsity Team" tabs were never fetched. If you want to change that policy (for example first name + grade for publicly listed varsity players), that needs your explicit approval and a separate review; this work does not prepare it.

## 8. What is missing and exactly what to ask for

1. Spring schedules (boys' volleyball, boys' tennis, golf, swimming & diving, track & field, girls' beach volleyball): the athletics office usually posts them on the public athletics calendar before the Feb 1 Spring Sports Athlete/Parent Meeting. Until then they stay "no schedule yet". Once the INGEST feature merges, you can instead share each team's Google Sheet (view access) with nuevai.updates@gmail.com or forward the schedule email to that address.
2. Same for boys'/girls' tennis, golf, soccer, cross-country: the official calendar page has no venues and no results; a Google Sheet like the basketball ones (Date / Home / Away / Location / Time) would add both.
3. Ask athletics@nuevaschool.org to confirm: (a) spring golf: boys-only or coed; (b) are Boys' Flag Football and Girls'/Boys' Ultimate Frisbee interscholastic teams in 2026-27; (c) is there a JV (or developmental) team for girls' basketball, soccer, golf, tennis (boys), volleyball (boys); (d) league for girls' beach volleyball and for track & field; (e) the boys varsity basketball opener date (Nov 17 vs 18); (f) head coach names for each team if they may be published.
4. Practice times for sports other than basketball (a "Practice Schedule" tab per team sheet would do).

## 9. Reproduce, and what to add to the daily workflow

- `python run_pipeline.py --stages live,sports,chunk,index` rebuilds everything (the `sports` stage fetches the athletics calendar months and the MaxPreps pages of the confirmed teams, politely, with a 20 h cache; `chunk` reads only the caches). `python scripts/flag_sports_rows.py` lists rows to flag (add `--apply`).
- NOT wired into the daily GitHub workflow (CAL owns workflows). Suggested addition to `.github/workflows/refresh-knowledge-base.yml`: run `LIVE_KINDS=team_schedule python run_pipeline.py --stages live,sports,calendar,chunk,index` (the athletics calendar page is public, no secret needed; the team sheets are "anyone with the link"), then `python scripts/flag_sports_rows.py --apply` so moved games never linger. The CI checkout has no data/pipeline pages, so the `chunk` stage there will only see what those stages just fetched: set `NUEVAI_DATA_DIR` to a cached directory or run `live,sports` in the same job.
- Checks: `python test_sports.py` (offline parsing/merge/precedence/dedupe/privacy/catalog-vs-knowledge tests), `python scripts/check_facts.py` (every knowledge token checked against stored source text; the new fact files verify against the `sports` stage's saved pages "Athletics sports offered (source data)" and "US Athletics Calendar (source data)", so run the stage once in a checkout before the audit there), `python scripts/audit_data.py` groups canon.*.
