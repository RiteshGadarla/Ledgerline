"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  TOUR_STEPS,
  type TourStep,
  markTourDone,
  stepMatchesRoute,
  tourDone,
} from "@/lib/tour";

type Rect = { top: number; left: number; width: number; height: number };

const PADDING = 6;
const CARD_WIDTH = 340;
const GAP = 12;

/** Where the callout sits relative to the thing it points at. Below by
 *  default, above when there is no room, and always inside the viewport. */
function placeCard(rect: Rect, viewport: { width: number; height: number }) {
  const below = rect.top + rect.height + GAP;
  const fitsBelow = below + 190 < viewport.height;
  const top = fitsBelow ? below : Math.max(GAP, rect.top - 190 - GAP);
  const left = Math.min(
    Math.max(GAP, rect.left + rect.width / 2 - CARD_WIDTH / 2),
    viewport.width - CARD_WIDTH - GAP,
  );
  return { top, left, pointsDown: !fitsBelow };
}

export function Tour() {
  const pathname = usePathname();
  const [running, setRunning] = useState(false);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const anchorRef = useRef<HTMLElement | null>(null);

  const step: TourStep | undefined = TOUR_STEPS[index];

  const finish = useCallback(() => {
    setRunning(false);
    setRect(null);
    markTourDone();
  }, []);

  // Start when another surface asks for it, and offer it once to an account
  // that has never seen it.
  useEffect(() => {
    function begin() {
      setIndex(0);
      setRunning(true);
    }
    window.addEventListener("ledgerline:tour", begin);
    return () => window.removeEventListener("ledgerline:tour", begin);
  }, []);

  // Find the current step's anchor, skipping steps whose anchor is not on this
  // page. Polled rather than measured once: a surface streams in, and the
  // element a step points at may appear a moment after the route does.
  useEffect(() => {
    if (!running || !step) return;

    let frame = 0;
    // Time, not frames. An anchor on a surface that fetches before it renders
    // is simply not there yet, and rAF is throttled to a crawl in a background
    // tab -- a frame count would give up on a slow API in well under a second
    // and skip a step whose control was about to appear.
    const waitingSince = performance.now();
    const PATIENCE_MS = 5000;

    function locate() {
      if (!step) return;
      if (!stepMatchesRoute(step, pathname)) {
        // The user navigated away from where this step lives. Advance to the
        // first step that belongs to where they actually are.
        const next = TOUR_STEPS.findIndex(
          (candidate, position) => position > index && stepMatchesRoute(candidate, pathname),
        );
        if (next >= 0) setIndex(next);
        else setRect(null);
        return;
      }

      const element = document.querySelector<HTMLElement>(`[data-tour="${step.anchor}"]`);
      if (!element) {
        // Wait for it, then skip rather than stall the tour on a control that
        // is not going to appear -- a report button on a run still in flight.
        if (performance.now() - waitingSince > PATIENCE_MS) {
          setIndex((current) => Math.min(current + 1, TOUR_STEPS.length - 1));
          return;
        }
        frame = window.requestAnimationFrame(locate);
        return;
      }

      anchorRef.current = element;
      const box = element.getBoundingClientRect();
      // Only on a real change. The anchor is re-measured every frame because
      // the page underneath keeps moving -- a stage scrolls, a run streams in
      // and reflows the row the spotlight is on -- but setting state on every
      // one of those frames would re-render the overlay sixty times a second
      // to draw the identical rectangle.
      setRect((current) =>
        current &&
        current.top === box.top &&
        current.left === box.left &&
        current.width === box.width &&
        current.height === box.height
          ? current
          : { top: box.top, left: box.left, width: box.width, height: box.height },
      );
      frame = window.requestAnimationFrame(locate);
    }

    frame = window.requestAnimationFrame(locate);
    return () => window.cancelAnimationFrame(frame);
  }, [running, step, pathname, index]);

  // Bring the anchor into view once per step, not on every frame.
  useEffect(() => {
    if (!running) return;
    anchorRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [running, index]);

  // A click-to-advance step hands control to the real control: the tour waits
  // for the actual button to be pressed and follows wherever it leads.
  useEffect(() => {
    if (!running || !step?.clickToAdvance) return;
    const element = anchorRef.current;
    if (!element) return;
    function advance() {
      setIndex((current) => Math.min(current + 1, TOUR_STEPS.length - 1));
    }
    element.addEventListener("click", advance);
    return () => element.removeEventListener("click", advance);
  }, [running, step, rect]);

  useEffect(() => {
    if (!running) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") finish();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, finish]);

  if (!running || !step || !rect) return null;

  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const card = placeCard(rect, viewport);
  const last = index === TOUR_STEPS.length - 1;

  return (
    // `pointer-events-none` on the root is what makes the cut-out a real hole.
    // The four scrim panes and the card each take their events back; without
    // it this full-screen container sits over the gap between them and eats
    // the very click the step is asking for.
    <div
      className="pointer-events-none fixed inset-0 z-[60]"
      role="dialog"
      aria-modal="true"
      aria-label="Product tour"
    >
      {/* The scrim is four panes around the anchor rather than one pane with a
          hole: a cut-out needs a mask, and four rectangles cannot leak a
          hover onto the element they surround. The anchor itself stays
          clickable, which is what makes "click here" mean it. */}
      <div className="pointer-events-auto absolute inset-x-0 top-0 bg-[rgb(6_9_14/0.55)]" style={{ height: Math.max(0, rect.top - PADDING) }} />
      <div className="pointer-events-auto absolute inset-x-0 bottom-0 bg-[rgb(6_9_14/0.55)]" style={{ top: rect.top + rect.height + PADDING }} />
      <div
        className="pointer-events-auto absolute bg-[rgb(6_9_14/0.55)]"
        style={{ top: rect.top - PADDING, height: rect.height + PADDING * 2, left: 0, width: Math.max(0, rect.left - PADDING) }}
      />
      <div
        className="pointer-events-auto absolute bg-[rgb(6_9_14/0.55)]"
        style={{ top: rect.top - PADDING, height: rect.height + PADDING * 2, left: rect.left + rect.width + PADDING, right: 0 }}
      />

      {/* The spotlight ring. Nothing catches pointer events, so the control
          underneath behaves exactly as it would without the tour. */}
      <div
        aria-hidden
        className="pointer-events-none absolute rounded-[4px] ring-2 ring-[color:var(--readout-hi)]"
        style={{
          top: rect.top - PADDING,
          left: rect.left - PADDING,
          width: rect.width + PADDING * 2,
          height: rect.height + PADDING * 2,
        }}
      />

      <div
        className="pointer-events-auto absolute rounded-[3px] border border-hairline-strong bg-surface p-4 shadow-[0_10px_40px_rgb(6_9_14/0.3)]"
        style={{ top: card.top, left: card.left, width: CARD_WIDTH }}
      >
        <div className="flex items-baseline justify-between gap-3">
          <span className="legend legend-hi">{step.title}</span>
          <span className="mono text-[11px] text-faint">
            {index + 1}/{TOUR_STEPS.length}
          </span>
        </div>
        <p className="mt-2 text-[13.5px] leading-relaxed text-muted">{step.body}</p>

        <div className="mt-3.5 flex items-center gap-2">
          {step.clickToAdvance ? (
            <>
              <span className="flex items-center gap-2 text-[13px] font-medium text-accent">
                <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
                Click the highlighted control
              </span>
              {/* A way past it that is not the click. The click is the point of
                  the step, but a step with only one way forward is a dead end
                  the moment anything is in the way of it -- which is exactly
                  what happened when the overlay was swallowing the click. */}
              <button
                type="button"
                onClick={() => setIndex(Math.min(index + 1, TOUR_STEPS.length - 1))}
                className="btn btn-sm !border-transparent text-faint"
              >
                Skip step
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => (last ? finish() : setIndex(index + 1))}
              className="btn btn-primary btn-sm"
            >
              {last ? "Done" : "Next"}
            </button>
          )}
          {index > 0 && !step.clickToAdvance && (
            <button type="button" onClick={() => setIndex(index - 1)} className="btn btn-sm">
              Back
            </button>
          )}
          <button type="button" onClick={finish} className="btn btn-sm ml-auto !border-transparent text-faint">
            Skip tour
          </button>
        </div>
      </div>
    </div>
  );
}

/** Anything can start the tour without threading state through the tree. */
export function startTour(): void {
  window.dispatchEvent(new Event("ledgerline:tour"));
}

export { tourDone };
