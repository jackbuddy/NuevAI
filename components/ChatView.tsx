'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, BookOpen, CalendarDays, Check, ChevronDown, Copy, GraduationCap, Link2, MapPin, RotateCw, ShieldCheck, Info, Square, SquarePen, ThumbsDown, ThumbsUp, Trophy, Zap } from 'lucide-react';
import { Markdown } from './Markdown';
import { Logo } from './Logo';
import { track } from '@/lib/analytics';
import { Message, loadGrade, saveGrade, streamChat, uid } from '@/lib/chat';
import { CalEvent, nowLA } from '@/lib/calendar';

const STARTERS = [
  { icon: Trophy, label: 'What sports are playing this season?' },
  { icon: GraduationCap, label: 'What engineering courses are offered?' },
  { icon: ShieldCheck, label: 'What is the cell phone policy?' },
  { icon: CalendarDays, label: 'When is the next school break?' },
  { icon: MapPin, label: 'How do I book a campus tour?' },
  { icon: BookOpen, label: 'What clubs can I join?' },
];
const STORE_KEY = 'nuevai.chat.v2';

export function ChatView({ pendingAsk, onAskConsumed, onOpenCalendar }: { pendingAsk: string | null; onAskConsumed: () => void; onOpenCalendar: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [waking, setWaking] = useState(false);
  const [greeting, setGreeting] = useState('Hello');
  const [today, setToday] = useState<CalEvent[] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<Message[]>([]);
  msgsRef.current = messages;
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);                       // follow the answer only while the reader is at the bottom
  const [away, setAway] = useState(false);          // reader scrolled up: offer a jump back
  const [announce, setAnnounce] = useState('');     // screen-reader status, not the streaming text itself
  const composing = useRef(false);
  const [grade, setGrade] = useState<number | null>(null);   // optional preference, browser-only
  const gradeRef = useRef<number | null>(null);
  gradeRef.current = grade;
  const [hydrated, setHydrated] = useState(false);   // do not save until the saved chat has been read back

  // restore conversation for this browser tab
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(STORE_KEY);
      // an answer that was still empty when the page was refreshed would show a loading skeleton forever: drop it
      if (raw) setMessages((JSON.parse(raw) as Message[]).filter((m) => m.role === 'user' || m.content).map((m) => ({ ...m, streaming: false })));
    } catch {}
    setGrade(loadGrade());
    setHydrated(true);
    const { date: d, hour: h } = nowLA();
    setGreeting(h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening');
    fetch(`/api/calendar?from=${d}&to=${d}&limit=200`).then((r) => r.ok ? r.json() : null).then((j) => j && setToday(j.events)).catch(() => {});
  }, []);
  useEffect(() => { if (!hydrated) return; try { sessionStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-24))); } catch {} }, [messages, hydrated]);
  const toBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (!el) return;
    const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth && !reduce ? 'smooth' : 'auto' });
  }, []);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stick.current = near;
    setAway((a) => (a === !near ? a : !near));
  };
  useEffect(() => { if (stick.current) toBottom(false); }, [messages, toBottom]);
  // desktop: ready to type. Touch screens: do not pop the keyboard up over the page on load.
  useEffect(() => { if (typeof matchMedia === 'function' && matchMedia('(pointer: fine)').matches && messages.length === 0) taRef.current?.focus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const t = taRef.current;
    if (!t) return;
    if (!input) { t.style.height = ''; return; }          // empty: let rows/min-height decide (no stale measurement)
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 160) + 'px';
  }, [input]);

  const send = useCallback(async (text: string, via: 'typed' | 'starter' | 'ask' | 'retry' = 'typed') => {
    const q = text.trim();
    if (!q || busy) return;
    const history = msgsRef.current.filter((m) => !m.error && !m.streaming && m.content).map((m) => ({ role: m.role, content: m.content }));
    const aId = uid();
    setMessages((p) => [...p, { id: uid(), role: 'user', content: q }, { id: aId, role: 'assistant', content: '', streaming: true, query: q }]);
    setInput('');
    stick.current = true;
    setAnnounce('Assistant is answering');
    setBusy(true);
    setWaking(false);
    const t0 = performance.now();
    let first = 0;
    track('chat_sent', { via, chars: q.length, turn: history.filter((m) => m.role === 'user').length + 1 });
    const wake = setTimeout(() => setWaking(true), 6500);
    const ac = new AbortController();
    abortRef.current = ac;
    const patch = (fn: (m: Message) => Message) => setMessages((p) => p.map((m) => (m.id === aId ? fn(m) : m)));
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          await streamChat(q, history, {
            onMeta: (m) => patch((x) => ({ ...x, sources: m.sources, cached: m.cached })),
            onDelta: (t) => { if (!first) { first = performance.now() - t0; setWaking(false); } patch((x) => ({ ...x, content: x.content + t })); },
            onDone: (d) => { setAnnounce('Answer ready'); patch((x) => ({ ...x, streaming: false, cached: d.cached, degraded: !!d.degraded, suggestions: d.suggestions ?? [], ms: Math.round(first), totalMs: Math.round(performance.now() - t0) })); },
          }, ac.signal, gradeRef.current);
          break;
        } catch (err: any) {
          // the model provider is briefly at its per-minute limit: wait a few seconds and try once more, quietly
          if (attempt === 0 && !first && /lot of questions/i.test(String(err?.message))) {
            track('chat_auto_retry');
            await new Promise((r) => setTimeout(r, 4500));
            continue;
          }
          throw err;
        }
      }
      track('chat_rendered', { first_ms: Math.round(first), total_ms: Math.round(performance.now() - t0) });
    } catch (e: any) {
      if (e?.name === 'AbortError') { setAnnounce('Stopped'); patch((x) => ({ ...x, streaming: false, stopped: true, ...(x.content ? {} : { error: true, content: 'Stopped before an answer arrived.' }) })); }
      else {
        setAnnounce('The assistant could not answer');
        track('chat_error_ui', { message: String(e?.message ?? e).slice(0, 120), after_ms: Math.round(performance.now() - t0) });
        patch((x) => ({ ...x, streaming: false, error: true, content: x.content || (/knowledge base/i.test(String(e?.message)) ? 'The assistant can’t reach the school’s knowledge base right now — this is a server setup issue, not your question. Please try again later.' : /lot of questions|temporarily unavailable/i.test(String(e?.message)) ? String(e.message).replace(' - ', ' — ') : String(e?.message).includes('timed out')
          ? 'The assistant took too long to respond. It may be waking up after being idle — please try again in a moment.'
          : 'I couldn’t reach the assistant just now. Check your connection and try again.') }));
      }
    } finally {
      clearTimeout(wake);
      setBusy(false);
      setWaking(false);
    }
  }, [busy]);

  useEffect(() => { if (pendingAsk) { send(pendingAsk, 'ask'); onAskConsumed(); } }, [pendingAsk, send, onAskConsumed]);

  const retry = (m: Message) => { if (m.query) { setMessages((p) => p.filter((x) => x.id !== m.id).slice(0, -1)); setTimeout(() => send(m.query!, 'retry'), 0); } };
  const clear = () => { abortRef.current?.abort(); setMessages([]); track('chat_cleared'); };
  const chooseGrade = (g: number | null) => { setGrade(g); saveGrade(g); };
  const empty = messages.length === 0;
  const games = (today ?? []).filter((e) => e.kind !== 'practice');
  const practices = (today ?? []).length - games.length;


  const gradePicker = (
    <div role="group" aria-label="Your grade (optional)" className="flex flex-wrap items-center gap-1.5">
      <span className="ink-2 mr-1 text-[12px] font-medium">My grade</span>
      {[9, 10, 11, 12].map((g) => (
        <button key={g} type="button" aria-pressed={grade === g} onClick={() => chooseGrade(grade === g ? null : g)}
                className={`tap-box h-8 min-w-8 rounded-full px-2.5 text-[12px] font-semibold transition max-sm:h-11 max-sm:min-w-11 ${grade === g ? 'bg-brand-900 text-white' : 'surface-2 text-brand-800 hover:bg-white hover:shadow-soft'}`}>{g}</button>
      ))}
      <span className="ink-2 text-[11px]">{grade ? 'Saved on this device only' : 'Optional, tailors course answers'}</span>
    </div>
  );

  const composer = (
    <form onSubmit={(e) => { e.preventDefault(); send(input); }}
          className="surface shadow-lift flex items-end gap-2 rounded-[1.75rem] p-2 pl-4 transition sm:pl-5 focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-500/10">
      <textarea ref={taRef} value={input} rows={1} maxLength={1000} onChange={(e) => setInput(e.target.value)}
                onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
                onKeyDown={(e) => {
                  // Enter sends, Shift+Enter is a new line; Enter that confirms an IME candidate (CJK, accents) must not send
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !composing.current && e.keyCode !== 229) { e.preventDefault(); send(input); }
                }}
                enterKeyHint="send" autoComplete="off"
                placeholder={empty ? 'Ask about courses, games, policies…' : 'Ask a follow-up…'} aria-label="Ask NuevAI"
                className="bare max-h-40 min-h-[2.75rem] min-w-0 flex-1 resize-none bg-transparent py-3 text-base outline-none placeholder:text-[var(--ink-2)] sm:text-[15px]" />
      {messages.length > 0 && (
        <button type="button" onClick={clear} aria-label="New chat" title="New chat" className="ink-2 tap-box h-11 shrink-0 gap-1.5 rounded-full px-3 text-xs font-semibold hover:bg-[var(--surface-2)] max-sm:w-11 max-sm:px-0"><SquarePen className="h-4 w-4" aria-hidden /><span className="hidden sm:inline">New chat</span></button>
      )}
      {busy ? (
        <button type="button" onClick={() => abortRef.current?.abort()} aria-label="Stop answering"
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-900 text-white transition hover:bg-brand-700">
          <Square className="h-4 w-4 fill-current" aria-hidden />
        </button>
      ) : (
        <button type="submit" disabled={!input.trim()} aria-label="Send"
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-900 text-white transition hover:bg-brand-700 disabled:opacity-40">
          <ArrowUp className="h-5 w-5" aria-hidden />
        </button>
      )}
    </form>
  );

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <p className="sr-only-live" role="status" aria-live="polite">{announce}</p>
      {empty ? (
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center px-4 pb-16 pt-10 md:px-6">
            <div className="animate-rise">
              <p className="mb-1 flex items-center gap-2 text-lg font-semibold text-brand-600"><Logo size={22} />{greeting}</p>
              <h1 className="text-balance text-4xl font-bold leading-[1.1] tracking-tight text-brand-900 md:text-[3.25rem]">
                What would you like to know?
              </h1>
            </div>

            <div className="animate-rise mt-8" style={{ animationDelay: '70ms' }}>{composer}</div>
            <div className="animate-rise mt-3 pl-1" style={{ animationDelay: '100ms' }}>{gradePicker}</div>

            <div className="animate-rise mt-5 flex flex-wrap gap-2" style={{ animationDelay: '140ms' }}>
              {STARTERS.map((s) => (
                <button key={s.label} onClick={() => { track('starter_click', { label: s.label }); send(s.label, 'starter'); }}
                        className="surface-2 tap-h inline-flex items-center gap-2 rounded-full border border-transparent px-4 py-2 text-[13px] font-medium text-brand-800 transition hover:border-[var(--line)] hover:bg-white hover:shadow-soft">
                  <s.icon className="h-3.5 w-3.5 text-brand-500" />{s.label}
                </button>
              ))}
            </div>

            {games.length > 0 && (
              <button onClick={() => { track('today_strip_click'); onOpenCalendar(); }}
                      className="animate-rise surface group mt-8 flex w-full items-center gap-3 rounded-2xl p-3.5 text-left transition hover:shadow-soft" style={{ animationDelay: '210ms' }}>
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-100 text-brand-700"><Zap className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] font-bold uppercase tracking-wider text-brand-500">Today</span>
                  <span className="block truncate text-sm font-semibold">{games.slice(0, 3).map((e) => e.summary.trim()).join('  ·  ')}</span>
                  <span className="ink-2 block text-xs">{games.length} event{games.length === 1 ? '' : 's'}{practices ? ` · ${practices} practices` : ''}</span>
                </span>
                <ChevronDown className="ink-2 h-4 w-4 -rotate-90 transition group-hover:translate-x-0.5" />
              </button>
            )}
          </div>
        </div>
      ) : (
        <>
          <div ref={scrollRef} onScroll={onScroll} className="scroll-thin relative min-h-0 flex-1 overflow-y-auto">
            <div role="log" aria-label="Conversation" className="mx-auto w-full max-w-3xl space-y-7 px-4 pb-6 pt-8 md:px-6">
              {messages.map((m, i) => (
                <div key={m.id}>
                  <Bubble m={m} waking={waking && !!m.streaming && !m.content} onRetry={() => retry(m)} />
                  {i === messages.length - 1 && !busy && !m.error && m.suggestions && m.suggestions.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2 pl-[38px]">
                      {m.suggestions.map((s) => (
                        <button key={s.label} onClick={() => { track('suggestion_click', { label: s.label }); s.action === 'calendar' ? onOpenCalendar() : s.query && send(s.query, 'starter'); }}
                                className="surface-2 tap-h rounded-full border border-transparent px-3.5 py-1.5 text-[13px] font-medium text-brand-800 transition hover:border-[var(--line)] hover:bg-white hover:shadow-soft">
                          {s.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              <div ref={endRef} />
            </div>
          </div>
          <div className="relative border-t hairline bg-white px-4 pb-safe pt-3 md:px-6">
            {away && (
              <button onClick={() => { stick.current = true; toBottom(true); }} aria-label="Jump to latest message"
                      className="surface shadow-lift tap-box absolute -top-14 left-1/2 h-11 w-11 -translate-x-1/2 rounded-full bg-white text-brand-800 hover:bg-[var(--surface-2)]">
                <ArrowDown className="h-4 w-4" aria-hidden />
              </button>
            )}
            <div className="mx-auto w-full max-w-3xl">
              {composer}
              <div className="mt-2 flex justify-center">{gradePicker}</div>
              <p className="ink-2 mt-2 text-center text-[11px] leading-snug">NuevAI can make mistakes — verify important dates. Anonymous analytics only; no account or personal data.</p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Bubble({ m, waking, onRetry }: { m: Message; waking: boolean; onRetry: () => void }) {
  const [copied, setCopied] = useState(false);
  const [showSrc, setShowSrc] = useState(false);
  const [fb, setFb] = useState<'up' | 'down' | undefined>(m.feedback);
  if (m.role === 'user') {
    return (
      <div className="animate-rise flex justify-end">
        <div className="wrap-any max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg bg-brand-900 px-4 py-2.5 text-[15px] leading-relaxed text-white">{m.content}</div>
      </div>
    );
  }
  return (
    <div className="animate-rise flex gap-3">
      <div className="mt-0.5 shrink-0"><Logo size={26} /></div>
      <div className="min-w-0 flex-1">
        {m.error ? (
          <div className="rounded-2xl border border-brand-200 bg-brand-50 p-4 text-sm">
            <p className="text-brand-800">{m.content}</p>
            <button onClick={onRetry} className="tap-box mt-3 gap-1.5 rounded-full bg-brand-900 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-700"><RotateCw className="h-3.5 w-3.5" />Try again</button>
          </div>
        ) : !m.content ? (
          <div className="surface rounded-2xl p-4">
            <div className="space-y-2.5">
              <div className="shimmer h-3 w-3/4 rounded-full" /><div className="shimmer h-3 w-1/2 rounded-full" /><div className="shimmer h-3 w-2/3 rounded-full" />
            </div>
            {waking && <p className="ink-2 mt-3 text-xs">Waking up the assistant — the first question after a quiet period can take up to a minute. After this it’s fast.</p>}
          </div>
        ) : (
          <div className={m.streaming ? 'caret' : ''} aria-busy={!!m.streaming}><Markdown streaming={!!m.streaming}>{m.content}</Markdown></div>
        )}
        {!m.streaming && !m.error && m.content && m.stopped && <p className="ink-2 mt-1 text-xs">Stopped.</p>}
        {!m.streaming && !m.error && m.content && m.degraded && (
          <p role="note" className="ink-2 mt-2 flex items-start gap-1.5 text-xs leading-snug"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />This is a quick summary from the school’s pages; the assistant couldn’t write a full answer just now. Try again in a minute for a better one.</p>
        )}

        {!m.streaming && !m.error && m.content && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <IconBtn label={copied ? 'Copied' : 'Copy'} onClick={() => { navigator.clipboard?.writeText(m.content); setCopied(true); track('copy_answer'); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}</IconBtn>
            <IconBtn label="Helpful" active={fb === 'up'} onClick={() => { setFb('up'); track('feedback', { value: 'up', query: m.query?.slice(0, 200), cached: !!m.cached }); }}><ThumbsUp className="h-3.5 w-3.5" /></IconBtn>
            <IconBtn label="Not helpful" active={fb === 'down'} onClick={() => { setFb('down'); track('feedback', { value: 'down', query: m.query?.slice(0, 200), cached: !!m.cached }); }}><ThumbsDown className="h-3.5 w-3.5" /></IconBtn>
            {m.cached && <span className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-1 text-[11px] font-semibold text-brand-700"><Zap className="h-3 w-3" />Instant</span>}
            {!m.cached && m.ms != null && <span className="ink-2 text-[11px]">{(m.ms / 1000).toFixed(1)}s</span>}
            {m.sources && m.sources.length > 0 && (
              <button onClick={() => { setShowSrc(!showSrc); track('sources_toggle', { open: !showSrc }); }} aria-expanded={showSrc} className="ink-2 tap-box ml-auto gap-1 rounded-full px-2 py-1 text-[11px] font-medium hover:bg-[var(--surface-2)]">
                <Link2 className="h-3 w-3" />{m.sources.length} source{m.sources.length === 1 ? '' : 's'}<ChevronDown className={`h-3 w-3 transition ${showSrc ? 'rotate-180' : ''}`} />
              </button>
            )}
          </div>
        )}
        {showSrc && (
          <ul className="surface mt-2 space-y-1 rounded-xl p-3 text-xs">
            {(m.sources ?? []).map((s, i) => (
              <li key={i} className="flex min-h-6 items-center gap-2">
                <Link2 className="h-3 w-3 shrink-0 text-brand-500" />
                {s.url ? <a className="truncate font-medium text-brand-600 hover:underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a> : <span className="truncate">{s.title || 'School calendar'}</span>}
                <span className="ink-2 ml-auto shrink-0">{s.type === 'calendar' ? 'calendar' : s.similarity ? `${Math.round(s.similarity * 100)}% match` : ''}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function IconBtn({ children, label, onClick, active }: { children: React.ReactNode; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button onClick={onClick} title={label} aria-label={label} aria-pressed={active === undefined ? undefined : !!active}
            className={`tap-box h-7 gap-1 rounded-full px-2 text-[11px] font-medium transition ${active ? 'bg-brand-900 text-white' : 'ink-2 hover:bg-[var(--surface-2)]'}`}>
      {children}{label === 'Copied' && label}
    </button>
  );
}
