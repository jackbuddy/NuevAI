/** The assistant answer currently on screen, so the header's "Report a problem" can say which answer a note is about.
 *  In memory only. Only used when the answer is recent (see FeedbackButton); nothing here is stored or sent on its own. */
interface Last { id: string; query: string; answer: string; at: number }
let last: Last | null = null;

export function noteLastAnswer(a: { id: string; query: string; answer: string }) { last = { ...a, at: Date.now() }; }
export function clearLastAnswer() { last = null; }
export function recentAnswer(maxAgeMs = 30 * 60_000): Last | null { return last && Date.now() - last.at <= maxAgeMs ? last : null; }
