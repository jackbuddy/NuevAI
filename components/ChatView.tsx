'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, BookOpen, CalendarDays, Check, Copy, GraduationCap, RotateCw, Sparkles, ThumbsDown, ThumbsUp, Trophy, Zap, MapPin, ShieldCheck, Link2, ChevronDown } from 'lucide-react';
import { Markdown } from './Markdown';
import { Logo } from './Logo';
import { track } from '@/lib/analytics';
import { Message, streamChat, uid } from '@/lib/chat';
import { CalEvent, iso, timeLabel } from '@/lib/calendar';

const STARTERS = [
  { icon: Trophy, label: 'What sports are playing this season?', hint: 'Teams & schedules', tone: 'from-orange-500/15 to-amber-400/5 text-orange-600 dark:text-orange-300' },
  { icon: GraduationCap, label: 'What engineering courses are offered?', hint: 'Course catalog', tone: 'from-brand-500/15 to-sky-400/5 text-brand-600 dark:text-brand-300' },
  { icon: ShieldCheck, label: 'What is the cell phone policy?', hint: 'Handbook & policies', tone: 'from-emerald-500/15 to-teal-400/5 text-emerald-600 dark:text-emerald-300' },
  { icon: CalendarDays, label: 'When is the next school break?', hint: 'School calendar', tone: 'from-fuchsia-500/15 to-pink-400/5 text-fuchsia-600 dark:text-fuchsia-300' },
  { icon: MapPin, label: 'How do I book a campus tour?', hint: 'Visit & admissions', tone: 'from-sky-500/15 to-cyan-400/5 text-sky-600 dark:text-sky-300' },
  { icon: BookOpen, label: 'What clubs can I join?', hint: 'Student life', tone: 'from-violet-500/15 to-indigo-400/5 text-violet-600 dark:text-violet-300' },
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

  // restore conversation for this browser tab
  useEffect(() => {
    try { const raw = sessionStorage.getItem(STORE_KEY); if (raw) setMessages(JSON.parse(raw).map((m: Message) => ({ ...m, streaming: false }))); } catch {}
    const h = new Date().getHours();
    setGreeting(h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening');
    const d = iso(new Date());
    fetch(`/api/calendar?from=${d}&to=${d}&limit=200`).then((r) => r.ok ? r.json() : null).then((j) => j && setToday(j.events)).catch(() => {});
  }, []);
  useEffect(() => { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-24))); } catch {} }, [messages]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages]);
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
      await streamChat(q, history, {
        onMeta: (m) => patch((x) => ({ ...x, sources: m.sources, cached: m.cached })),
        onDelta: (t) => { if (!first) { first = performance.now() - t0; setWaking(false); } patch((x) => ({ ...x, content: x.content + t })); },
        onDone: (d) => patch((x) => ({ ...x, streaming: false, cached: d.cached, ms: Math.round(first), totalMs: Math.round(performance.now() - t0) })),
      }, ac.signal);
      track('chat_rendered', { first_ms: Math.round(first), total_ms: Math.round(performance.now() - t0) });
    } catch (e: any) {
      if (e?.name === 'AbortError') { patch((x) => ({ ...x, streaming: false })); }
      else {
        track('chat_error_ui', { message: String(e?.message ?? e).slice(0, 120), after_ms: Math.round(performance.now() - t0) });
        patch((x) => ({ ...x, streaming: false, error: true, content: x.content || (String(e?.message).includes('timed out')
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
  const empty = messages.length === 0;
  const games = (today ?? []).filter((e) => e.kind !== 'practice');
  const practices = (today ?? []).length - games.length;

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 pb-6 pt-6 md:px-6">
          {empty ? (
            <div className="animate-rise">
              <section className="relative overflow-hidden rounded-[2rem] px-6 pb-7 pt-8 text-center md:px-12 md:pb-9 md:pt-10">
                <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
                  <div className="absolute -left-16 -top-24 h-72 w-72 animate-float rounded-full blur-3xl" style={{ background: 'var(--glow-a)' }} />
                  <div className="absolute -right-10 top-10 h-64 w-64 animate-float-slow rounded-full blur-3xl" style={{ background: 'var(--glow-b)' }} />
                  <div className="absolute bottom-[-6rem] left-1/3 h-72 w-72 animate-float rounded-full blur-3xl" style={{ background: 'var(--glow-c)' }} />
                  <div className="bg-dots absolute inset-0 opacity-60 [mask-image:radial-gradient(ellipse_at_center,black,transparent_72%)]" />
                </div>
                <div className="mx-auto mb-4 flex justify-center"><Logo size={52} /></div>
                <p className="mb-2 text-sm font-medium tracking-wide text-brand-600 dark:text-brand-300">{greeting}</p>
                <h1 className="font-display text-balance text-4xl font-semibold leading-[1.08] tracking-tight md:text-5xl">
                  Ask anything about <span className="bg-gradient-to-r from-brand-600 via-brand-500 to-amber-500 bg-clip-text text-transparent">Nueva</span>
                </h1>
                <p className="ink-2 mx-auto mt-4 max-w-xl text-balance text-base md:text-lg">
                  Courses, the school calendar, game schedules, policies and campus resources, answered from the school’s own documents.
                </p>
              </section>

              {games.length > 0 && (
                <button onClick={() => { track('today_strip_click'); onOpenCalendar(); }}
                        className="surface shadow-soft group mx-auto mb-6 flex w-full items-center gap-3 rounded-2xl p-3 text-left transition hover:-translate-y-0.5 hover:shadow-lift md:p-4">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-amber-400/15 text-amber-600 dark:text-amber-300"><Zap className="h-5 w-5" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-300">Today at Nueva</span>
                    <span className="block truncate text-sm font-medium">{games.slice(0, 3).map((e) => e.summary.trim()).join('  ·  ')}</span>
                    <span className="ink-2 block text-xs">{games.length} event{games.length === 1 ? '' : 's'}{practices ? ` · ${practices} practices` : ''} · tap to open the calendar</span>
                  </span>
                  <ChevronDown className="ink-2 h-4 w-4 -rotate-90 transition group-hover:translate-x-0.5" />
                </button>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                {STARTERS.map((s, i) => (
                  <button key={s.label} onClick={() => { track('starter_click', { label: s.label }); send(s.label, 'starter'); }}
                          style={{ animationDelay: `${i * 60}ms` }}
                          className="surface shadow-soft group animate-rise flex items-start gap-3 rounded-2xl p-4 text-left transition hover:-translate-y-0.5 hover:shadow-lift">
                    <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br ${s.tone}`}><s.icon className="h-5 w-5" /></span>
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold leading-snug">{s.label}</span>
                      <span className="ink-2 mt-0.5 block text-xs">{s.hint}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m) => <Bubble key={m.id} m={m} waking={waking && m.streaming && !m.content} onRetry={() => retry(m)} />)}
              <div ref={endRef} />
            </div>
          )}
        </div>
      </div>

      <div className="relative border-t hairline bg-gradient-to-t from-[var(--bg)] via-[var(--bg)] to-transparent px-4 pb-4 pt-3 md:px-6">
        <div className="mx-auto w-full max-w-3xl">
          <form onSubmit={(e) => { e.preventDefault(); send(input); }}
                className="surface shadow-lift flex items-end gap-2 rounded-3xl p-2 pl-4 transition focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-500/10">
            <textarea ref={taRef} value={input} rows={1} maxLength={1000} onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); } }}
                      placeholder="Ask about courses, games, policies…" aria-label="Ask NuevAI"
                      className="max-h-40 min-h-[2.75rem] flex-1 resize-none bg-transparent py-3 text-[15px] outline-none placeholder:text-[var(--ink-2)]" />
            {messages.length > 0 && (
              <button type="button" onClick={clear} className="ink-2 hidden rounded-full px-3 py-2 text-xs font-medium hover:bg-[var(--surface-2)] sm:block">New chat</button>
            )}
            <button type="submit" disabled={busy || !input.trim()} aria-label="Send"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-700 text-white shadow-soft transition hover:bg-brand-600 disabled:opacity-35">
              <ArrowUp className="h-5 w-5" />
            </button>
          </form>
          <p className="ink-2 mt-2 text-center text-[11px]">NuevAI can make mistakes — verify important dates. Anonymous analytics only; no account or personal data.</p>
        </div>
      </div>
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
        <div className="max-w-[85%] rounded-3xl rounded-br-lg bg-brand-700 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-soft">{m.content}</div>
      </div>
    );
  }
  return (
    <div className="animate-rise flex gap-3">
      <div className="mt-1 shrink-0"><Logo size={30} /></div>
      <div className="min-w-0 flex-1">
        {m.error ? (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/5 p-4 text-sm">
            <p className="text-rose-700 dark:text-rose-300">{m.content}</p>
            <button onClick={onRetry} className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-rose-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-rose-500"><RotateCw className="h-3.5 w-3.5" />Try again</button>
          </div>
        ) : !m.content ? (
          <div className="surface rounded-2xl p-4">
            <div className="space-y-2.5">
              <div className="shimmer h-3 w-3/4 rounded-full" /><div className="shimmer h-3 w-1/2 rounded-full" /><div className="shimmer h-3 w-2/3 rounded-full" />
            </div>
            {waking && <p className="ink-2 mt-3 text-xs">Waking up the assistant — the first question after a quiet period can take up to a minute. After this it’s fast.</p>}
          </div>
        ) : (
          <div className={m.streaming ? 'caret' : ''}><Markdown>{m.content}</Markdown></div>
        )}

        {!m.streaming && !m.error && m.content && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <IconBtn label={copied ? 'Copied' : 'Copy'} onClick={() => { navigator.clipboard?.writeText(m.content); setCopied(true); track('copy_answer'); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}</IconBtn>
            <IconBtn label="Helpful" active={fb === 'up'} onClick={() => { setFb('up'); track('feedback', { value: 'up', query: m.query?.slice(0, 200), cached: !!m.cached }); }}><ThumbsUp className="h-3.5 w-3.5" /></IconBtn>
            <IconBtn label="Not helpful" active={fb === 'down'} onClick={() => { setFb('down'); track('feedback', { value: 'down', query: m.query?.slice(0, 200), cached: !!m.cached }); }}><ThumbsDown className="h-3.5 w-3.5" /></IconBtn>
            {m.cached && <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/15 px-2 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300"><Zap className="h-3 w-3" />Instant</span>}
            {!m.cached && m.ms != null && <span className="ink-2 text-[11px]">{(m.ms / 1000).toFixed(1)}s</span>}
            {m.sources && m.sources.length > 0 && (
              <button onClick={() => { setShowSrc(!showSrc); track('sources_toggle', { open: !showSrc }); }} className="ink-2 ml-auto inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium hover:bg-[var(--surface-2)]">
                <Link2 className="h-3 w-3" />{m.sources.length} source{m.sources.length === 1 ? '' : 's'}<ChevronDown className={`h-3 w-3 transition ${showSrc ? 'rotate-180' : ''}`} />
              </button>
            )}
          </div>
        )}
        {showSrc && (
          <ul className="surface mt-2 space-y-1 rounded-xl p-3 text-xs">
            {(m.sources ?? []).map((s, i) => (
              <li key={i} className="flex items-center gap-2">
                <Sparkles className="h-3 w-3 shrink-0 text-brand-500" />
                {s.url ? <a className="truncate font-medium text-brand-600 hover:underline dark:text-brand-300" href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a> : <span className="truncate">{s.title || 'School calendar'}</span>}
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
    <button onClick={onClick} title={label} aria-label={label}
            className={`inline-flex h-7 items-center gap-1 rounded-full px-2 text-[11px] font-medium transition ${active ? 'bg-brand-600 text-white' : 'ink-2 hover:bg-[var(--surface-2)]'}`}>
      {children}{label === 'Copied' && label}
    </button>
  );
}
