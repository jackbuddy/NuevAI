export function Logo({ size = 32 }: { size?: number }) {
  return (
    <span className="relative inline-flex items-center justify-center rounded-xl shadow-lift" style={{ width: size, height: size, background: 'linear-gradient(135deg,#1c65a8 0%,#08519c 55%,#0b2a50 100%)' }}>
      <svg viewBox="0 0 24 24" width={size * 0.58} height={size * 0.58} fill="none" aria-hidden>
        <path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9L12 2.5z" fill="#fff" />
        <path d="M18.5 15l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9.9-2.6z" fill="#fbbf24" />
      </svg>
    </span>
  );
}
