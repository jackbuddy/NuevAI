export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-grid place-items-center rounded-full bg-brand-900 text-white" style={{ width: size, height: size }} aria-hidden>
      <svg viewBox="0 0 24 24" width={size * 0.56} height={size * 0.56} fill="currentColor">
        <path d="M12 2.5l2 6.2 6.2 2-6.2 2-2 6.3-2-6.3-6.2-2 6.2-2 2-6.2z" />
      </svg>
    </span>
  );
}
