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
    id: "to-data",
    anchor: "rail-data",
    route: "/run",
    title: "Nothing to run yet",
    body: "Your account opens empty — nothing was generated on your behalf. Click Data and we will make a corpus worth running.",
    clickToAdvance: true,
  },
  {
    id: "data-choices",
    anchor: "data-choices",
    route: "/data",
    title: "What a dataset is",
    body: "Four files — invoices raised, payments captured, payouts settled, credits that landed. Bring your own as CSV, XLSX or PDF, or generate a corpus with a known answer key.",
  },
  {
    id: "data-generate",
    anchor: "data-generate",
    route: "/data",
    title: "Generate one",
    body: "Take the generated route first: it is the only kind that ships with the truth, so precision and recall are measured rather than asserted. Click here.",
    clickToAdvance: true,
  },
  {
    id: "generate-form",
    anchor: "data-generate-form",
    route: "/data",
    title: "Seed and size are yours",
    body: "The seed fixes the books: the same seed and size generate the same records, which is what lets one run's output hash be checked against another. Size sets how many records carry each of the 11 difficulty classes.",
  },
  {
    id: "generate-submit",
    anchor: "data-generate-submit",
    route: "/data",
    title: "Click here to build it",
    body: "It plants the difficulties, writes an answer key the engine never sees, and hands you back to the console with the corpus selected.",
    clickToAdvance: true,
  },
  {
    id: "dataset",
    anchor: "run-dataset",
    route: "/run",
    title: "The corpus you just made",
    body: "Selected and ready. The chips beside it say which of the four roles it carries; all four is what makes a full chain walkable.",
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
    title: "Now bring your own books",
    body: "The same surface takes CSV, XLSX or PDF per role. Column names are resolved against a table first and a model is asked only about the ones left over. Scored runs need an answer key, so real books report matches and exceptions without accuracy.",
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

/**
 * Whether the tour is running right now.
 *
 * A module flag rather than context: exactly one Tour is mounted, and the one
 * caller outside it is the Data surface, which has to know whether a freshly
 * generated dataset should stay on screen for inspection (the normal case) or
 * hand the user back to the console to run it (the tour, which promised
 * exactly that on the previous step).
 */
let running = false;

export function setTourRunning(value: boolean): void {
  running = value;
}

export function tourRunning(): boolean {
  return running;
}

export function stepMatchesRoute(step: TourStep, pathname: string): boolean {
  return step.route === "/runs/" ? pathname.startsWith("/runs/") : pathname === step.route;
}
