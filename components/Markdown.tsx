'use client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { track } from '@/lib/analytics';

/** While streaming, hide syntax that is still incomplete so the user never sees a half-built
 *  table, a dangling "[label](ht", or stray ** markers. Only the unfinished tail is trimmed. */
export function stabilize(md: string): string {
  let t = md;
  // Incomplete trailing table: drop a half-typed last row, and a lone header row with no body yet.
  const lines = t.split('\n');
  if (/^\s*\|/.test(lines[lines.length - 1]) && !/\|\s*$/.test(lines[lines.length - 1])) lines.pop();
  let n = 0;
  for (let i = lines.length - 1; i >= 0 && /^\s*\|/.test(lines[i]); i--) n++;
  if (n > 0 && n < 3) lines.splice(lines.length - n, n); // header (+ separator) only: wait for a body row
  t = lines.join('\n');
  // Unfinished link: "[label](partial-url" or "[label" at the very end.
  t = t.replace(/\[[^\]\n]*\]\([^)\n]*$/, (m) => m.slice(1, m.indexOf(']')));
  t = t.replace(/\[[^\]\n]*$/, (m) => m.slice(1));
  // Unpaired emphasis markers at the tail.
  if ((t.match(/\*\*/g) || []).length % 2) t = t.replace(/\*\*(?![\s\S]*\*\*)/, '');
  return t;
}

export function Markdown({ children, className = '', streaming = false }: { children: string; className?: string; streaming?: boolean }) {
  const text = streaming ? stabilize(children) : children;
  return (
    <div className={`prose prose-sm max-w-none break-words [overflow-wrap:anywhere] prose-ol:my-2 prose-li:marker:text-brand-500 [&_ul_ul]:my-1 [&_ol_ol]:my-1 prose-p:my-2 prose-p:leading-relaxed prose-ul:my-2 prose-li:my-0.5 prose-a:text-brand-600 prose-a:font-medium prose-a:no-underline hover:prose-a:underline prose-strong:font-semibold ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" data-tracked="1"
               onClick={() => { try { track('answer_link_click', { host: new URL(href ?? '').host, label: String(children).slice(0, 60) }); } catch {} }}>
              {children}
            </a>
          ),
          table: ({ children }) => (<div className="my-2 max-w-full overflow-x-auto rounded-lg border border-[var(--line)]"><table className="my-0 w-full text-left text-[13px]">{children}</table></div>),
          th: ({ children }) => <th className="whitespace-nowrap bg-brand-50 !px-2.5 !py-1.5 font-semibold">{children}</th>,
          td: ({ children }) => <td className="!px-2.5 !py-1.5 align-top">{children}</td>,
          h1: ({ children }) => <p className="font-semibold">{children}</p>,
          h2: ({ children }) => <p className="font-semibold">{children}</p>,
          h3: ({ children }) => <p className="font-semibold">{children}</p>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
