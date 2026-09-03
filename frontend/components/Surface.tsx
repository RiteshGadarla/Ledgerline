"use client";

import { useEffect } from "react";
import { StatusStrip } from "@/components/StatusStrip";

/**
 * Every signed-in surface has the same three parts: a context bar closed by
 * one hard rule, a scrolling stage, and the status strip pinned to the
 * bottom. Nothing is centred in a fixed column: the stage fills the well and
 * its own grids decide how the width is spent.
 */
export function Surface({
  crumb,
  title,
  tools,
  strip,
  band,
  children,
}: {
  crumb: string;
  title: React.ReactNode;
  tools?: React.ReactNode;
  strip?: { label: string; value?: React.ReactNode; tone?: string }[];
  /** Sits between the context bar and the stage: the run's channel tabs. */
  band?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ContextBar crumb={crumb} title={title} tools={tools} />
      {band}
      <Stage>{children}</Stage>
      <StatusStrip segments={strip ?? []} />
    </div>
  );
}

export function ContextBar({
  crumb,
  title,
  tools,
}: {
  crumb: string;
  title: React.ReactNode;
  tools?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[54px] shrink-0 flex-wrap items-center gap-x-3.5 gap-y-1 border-b border-hard bg-surface px-[clamp(0.875rem,1.6vw,1.625rem)] py-2">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="legend">{crumb}</span>
        <span aria-hidden className="text-faint">
          /
        </span>
        {title}
      </div>
      {tools && <div className="ml-auto flex items-center gap-2">{tools}</div>}
    </div>
  );
}

export function Stage({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-[clamp(0.875rem,1.6vw,1.625rem)]">
      {children}
    </div>
  );
}

/** The head of an instrument face: legend, optional right-hand mono note. */
export function PanelHead({ legend, note }: { legend: string; note?: React.ReactNode }) {
  return (
    <div className="panel-head">
      <span className="legend legend-hi">{legend}</span>
      {note && <span className="mono ml-auto text-[12px] text-faint">{note}</span>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 py-6 text-sm text-muted" role="status">
      <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
      {label}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs leading-relaxed text-muted">{body}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/**
 * A centred dialog over a scrim. Same geometry as a panel -- hairline frame,
 * one hard rule under the header -- so it reads as an instrument face lifted
 * off the stage rather than a floating card. The header and footer are fixed
 * and only the body scrolls, so a long table never pushes the controls off
 * the screen.
 */
export function Modal({
  title,
  ariaLabel,
  meta,
  size = "md",
  height = "auto",
  padded = true,
  footer,
  onClose,
  children,
}: {
  title: React.ReactNode;
  /** The dialog's accessible name when `title` is markup rather than a string. */
  ariaLabel?: string;
  /** Right of the title, before the close button: status pills, counts. */
  meta?: React.ReactNode;
  size?: "md" | "lg";
  /** "tall" pins the dialog to a fixed height so a two-row table doesn't
   *  collapse it; "auto" lets it size to its content. */
  height?: "auto" | "tall";
  /** Off for content that brings its own edges, like a full-bleed table. */
  padded?: boolean;
  footer?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <>
      <div
        onClick={onClose}
        aria-hidden
        className="fixed inset-0 z-40 bg-[rgb(6_9_14/0.32)]"
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={ariaLabel ?? (typeof title === "string" ? title : undefined)}
          className={
            "panel flex max-h-full w-full flex-col overflow-hidden bg-surface " +
            (size === "lg" ? "max-w-[74rem] " : "max-w-3xl ") +
            (height === "tall" ? "h-[85vh]" : "")
          }
        >
          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-hard px-5 py-3">
            {typeof title === "string" ? <span className="eyebrow">{title}</span> : title}
            {meta}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="ml-auto grid h-7 w-7 place-items-center rounded-[3px] text-muted hover:bg-surface-hover hover:text-foreground"
            >
              <svg
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                aria-hidden
              >
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
          {/* Padded bodies scroll as one block; unpadded ones are handed the
              space and scroll their own regions, so a sticky table header or
              a tab bar inside stays put. */}
          <div
            className={
              "min-h-0 flex-1 " + (padded ? "overflow-auto p-5" : "flex flex-col overflow-hidden")
            }
          >
            {children}
          </div>
          {footer && (
            <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-t border-hairline bg-sunk px-5 py-2.5">
              {footer}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
