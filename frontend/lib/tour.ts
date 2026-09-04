/**
 * The guided tour a new account is offered.
 *
 * Steps are anchored to `data-tour` attributes rather than CSS selectors: a
 * class name is a styling decision that moves, and a tour that breaks silently
 * when someone restyles a button is worse than no tour. A step whose anchor is
 * not on the page is skipped rather than blocking, so the tour survives a
 * surface that has not loaded yet or a run that has not finished.
 */

export type TourStep = {
  id: string;
  /** The `data-tour` value this step points at. */
  anchor: string;
  title: string;
  body: string;
  /** Which route the step belongs to. `"/runs/"` matches any run's surfaces. */
  route: string;
  /**
   * An action step: the tour waits for the user to click the anchor rather
   * than offering Next, and follows wherever that click leads. This is the
   * "click here" the tour exists for -- reading about a button is not the
   * same as pressing one.
   */
  clickToAdvance?: boolean;
};

export const TOUR_STEPS: TourStep[] = [
  {
    id: "rail",
    anchor: "rail-run",
    route: "/run",
    title: "The console",
    body: "Two places: Run closes the books, Data holds the files you close them against. Everything else is a view of one run.",
  },
  {
    id: "dataset",
    anchor: "run-dataset",
    route: "/run",
    title: "Your demo corpus",
    body: "Your account opens with 400 generated records — invoices, payments, settlements and bank lines. 15% carry a seeded difficulty, and it ships with an answer key the engine never sees.",
  },
  {
    id: "mutations",
    anchor: "run-mutations",
    route: "/run",
    title: "Break it on purpose",
    body: "Optional. Each corruption is applied to a chain that would otherwise have tied out, so the exception it produces is attributable to the sabotage rather than to a difficulty the corpus already had.",
  },
  {
    id: "close",
    anchor: "run-submit",
    route: "/run",
    title: "Click here to start",
    body: "The run executes in a separate worker. The stages stream back as they happen, so you watch it work rather than waiting on a spinner.",
    clickToAdvance: true,
  },
  {
    id: "scoreboard",
    anchor: "run-tabs",
    route: "/runs/",
    title: "Four views of one run",
    body: "Scoreboard is the verdict. Chain walks each rupee from invoice to bank line. Exceptions is everything that would not tie out. Cash position projects what is still owed.",
  },
  {
    id: "exceptions",
    anchor: "tab-exceptions",
    route: "/runs/",
    title: "The honest list",
    body: "Click through when the run finishes. Every item carries the check it failed and the evidence behind it. A reconciliation reporting none on real books is not finished, it is lying.",
  },
  {
    id: "report",
    anchor: "run-report",
    route: "/runs/",
    title: "Take it with you",
    body: "The whole run as a PDF — verdict, exceptions, cash position, and what reproduces it — for the reader who will never be handed a URL.",
  },
  {
    id: "lyra",
    anchor: "lyra-open",
    route: "/runs/",
    title: "Ask about this run",
    body: "Lyra answers from this run's stored result and cites what it read. It cannot see anything the run did not produce.",
  },
  {
    id: "data",
    anchor: "rail-data",
    route: "/runs/",
    title: "Bring your own books",
    body: "Upload CSV, XLSX or PDF per role. Column names are resolved against a table first and a model is asked only about the ones left over.",
  },
];

const STORAGE_KEY = "ledgerline-tour";

/** Whether this browser has finished or dismissed the tour before. */
export function tourDone(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "done";
  } catch {
    // A private window, or site data blocked. Offering the tour again is a
    // far smaller cost than crashing the surface it sits on.
    return false;
  }
}

export function markTourDone(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, "done");
  } catch {
    /* nothing to do: the tour simply offers itself again next time */
  }
}

export function clearTourDone(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* as above */
  }
}

export function stepMatchesRoute(step: TourStep, pathname: string): boolean {
  return step.route === "/runs/" ? pathname.startsWith("/runs/") : pathname === step.route;
}
