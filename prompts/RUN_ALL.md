Run the NuevAI improvement queue in this session, fully unattended.

1. Read prompts/00_STANDING_RULES.md. These rules apply to everything below.
2. Switch to branch `auto/queue` (create from main if missing).
3. For N = 01 to 12, in order, one at a time (never in parallel): start a fresh subagent (Agent tool, general-purpose) whose prompt is the full text of prompts/00_STANDING_RULES.md followed by the full text of prompts/NN_*.md, and tell it to work in ~/Developer/NuevAI. Wait for it to finish. Subagents get a clean context, so the files QUEUE_LOG.md, NEEDS_USER.md and data/eval/ are the hand-off between prompts. After each one, read the tail of QUEUE_LOG.md, confirm the gates passed and a commit exists on auto/queue; if a prompt failed or left the tree dirty, make one repair attempt, otherwise revert its changes, note it in QUEUE_LOG.md, and continue to the next.
4. Do not ask me anything. Do not push to main or merge.
5. When all 12 are done, tell me the score before/after, what needs me (NEEDS_USER.md), and the exact commands to review and merge auto/queue.
