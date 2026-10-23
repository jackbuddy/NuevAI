'use client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { track } from '@/lib/analytics';

export function Markdown({ children, className = '' }: { children: string; className?: string }) {
  return (
    <div className={`prose prose-sm max-w-none prose-p:my-2 prose-p:leading-relaxed prose-ul:my-2 prose-li:my-0.5 prose-a:text-brand-600 prose-a:font-medium prose-a:no-underline hover:prose-a:underline prose-strong:font-semibold ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" data-tracked="1"
               onClick={() => { try { track('answer_link_click', { host: new URL(href ?? '').host, label: String(children).slice(0, 60) }); } catch {} }}>
              {children}
            </a>
          ),
          h1: ({ children }) => <p className="font-semibold">{children}</p>,
          h2: ({ children }) => <p className="font-semibold">{children}</p>,
          h3: ({ children }) => <p className="font-semibold">{children}</p>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
