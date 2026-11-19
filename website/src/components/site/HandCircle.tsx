import type { ReactNode } from "react";

/** A word circled with a purple pen stroke, like the titles in Utter's own windows. */
export function HandCircle({ children, marks = true }: { children: ReactNode; marks?: boolean }) {
  return (
    <span className="relative inline-block px-[0.18em]">
      <span className="relative z-10">{children}</span>
      <svg
        className="pointer-events-none absolute -inset-x-[0.12em] -inset-y-[0.18em] h-[calc(100%+0.36em)] w-[calc(100%+0.24em)] overflow-visible"
        viewBox="0 0 200 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M 38 88 C 8 80, 2 46, 22 26 C 48 2, 150 -2, 182 22 C 204 40, 196 78, 160 90 C 120 102, 52 98, 26 78"
          fill="none"
          stroke="var(--color-violet)"
          strokeWidth="4.5"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {marks && (
        <svg
          className="pointer-events-none absolute -top-[0.42em] -right-[0.35em] h-[0.55em] w-[0.55em]"
          viewBox="0 0 30 30"
          aria-hidden="true"
        >
          <path d="M6 20 L4 4 M13 22 L24 8 M17 27 L29 22" stroke="var(--color-violet)" strokeWidth="3" strokeLinecap="round" />
        </svg>
      )}
    </span>
  );
}

/** A slightly wobbly pen underline. */
export function HandUnderline({ className = "" }: { className?: string }) {
  return (
    <svg className={`pointer-events-none ${className}`} viewBox="0 0 200 20" preserveAspectRatio="none" aria-hidden="true">
      <path d="M4 12 C 50 4, 120 6, 196 9" fill="none" stroke="var(--color-violet)" strokeWidth="4" strokeLinecap="round" />
      <path d="M40 18 C 70 13, 100 14, 130 15" fill="none" stroke="var(--color-violet)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
