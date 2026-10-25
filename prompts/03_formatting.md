# 03 - Consistent, polished answer formatting

Problem: some answers are formatted unevenly. Audit 60+ live-style answers across categories and define one house style, then enforce it.
1. Write the style in SYSTEM_PERSONA: lead with the direct answer in the first sentence; short; bullets only for lists of 3+; schedules/courses as compact tables or "Day, date - time - place" lines; bold only key facts; links as `[label](exact-url)` taken from sources only; dates like "Thu, Oct 22"; times like "8:55-10:10 AM"; no filler openers, no "As an AI", no raw source titles, no emoji; say "I don't have that" plus the best next step (office phone/link) when unsure.
2. Check the frontend Markdown renderer (components/Markdown.tsx) handles tables, nested lists, links (open in new tab, rel noopener), long words, and mobile width at 375px. Test with the browser pane.
3. Add `format_lint()` in a test script that flags: answers over ~180 words for simple questions, headings in short answers, bare URLs, missing links when a link card exists, sources leaked into text, unclosed markdown. Run it over the golden set outputs and drive violations to near zero.
4. Check streaming does not flash broken markdown mid-stream (e.g. half a table); fix if it does.
