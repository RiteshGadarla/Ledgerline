"use client";

import { useId } from "react";

/**
 * A small "i" that explains a figure on hover or focus.
 *
 * Used where a number needs its provenance stated rather than assumed: the
 * accuracy panel only exists because the corpus shipped with an answer key,
 * and a reader who does not know that cannot tell a measured 1.000 from a
 * flattering one.
 *
 * Hover alone would leave it unreachable by keyboard, so the trigger is a real
 * button and the panel opens on focus too. `aria-describedby` hands the same
 * text to a screen reader that a pointer gets by hovering.
 */
export function InfoDot({ label, children }: { label: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <span className="group relative inline-flex items-center">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="grid h-4 w-4 place-items-center rounded-full border border-hairline-strong text-[10px] font-semibold leading-none text-faint transition-colors hover:border-accent hover:text-accent focus-visible:border-accent focus-visible:text-accent"
      >
        i
      </button>
      <span
        role="tooltip"
        id={id}
        // Opens down and to the LEFT. The dot lives at the right edge of a
        // panel header, so anchoring the panel's left edge to the dot would
        // send it straight off the side of the screen -- which is exactly what
        // it did. Right-anchored, it grows back across the page instead, and
        // the width is capped so it still fits a narrow viewport.
        className="pointer-events-none absolute right-0 top-6 z-30 w-[min(19rem,70vw)] rounded-[3px] border border-hairline-strong bg-surface p-3 text-left text-[12.5px] leading-relaxed text-muted opacity-0 shadow-[0_6px_20px_rgb(16_20_26/0.12)] transition-opacity duration-100 group-focus-within:opacity-100 group-hover:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}
