/**
 * The difficulty classes the generator plants, and how a person in finance
 * reads them.
 *
 * Shared rather than duplicated: the scoreboard names these classes when it
 * scores recall against them, and the generate dialog names the same ones
 * while it builds the corpus. Two lists would drift, and the second one to
 * drift would be the one nobody was reading.
 *
 * Ordering is the order they are shown while generating, easiest first, so
 * the list reads as a corpus being built up rather than a set being shuffled.
 */

/** GST and UTR are acronyms rather than words; lowercased they read as typos. */
const ACRONYMS: Record<string, string> = { gst: "GST", utr: "UTR" };

export function formatClassName(name: string): string {
  const words = name
    .split("_")
    .map((word) => ACRONYMS[word] ?? word)
    .join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type DifficultyClass = {
  name: string;
  /** What the generator actually plants, in one line. */
  blurb: string;
};

export const DIFFICULTY_CLASSES: DifficultyClass[] = [
  { name: "clean", blurb: "Ties out on an exact reference and an exact amount." },
  { name: "fee_gst_delta", blurb: "The payout is the gross less the gateway fee and the GST on it." },
  { name: "partial_split", blurb: "One bank credit covers several invoices at once." },
  { name: "refund_in_batch", blurb: "A refund settles inside the same batch it was captured in." },
  { name: "chargeback", blurb: "A disputed payment is clawed back after it settled." },
  { name: "duplicate_payment", blurb: "The same capture posted twice, under two gateway ids." },
  { name: "date_outside_window", blurb: "The credit lands weeks after the settlement date claims." },
  { name: "narration_missing_utr", blurb: "The statement prints the credit without ever naming its reference." },
  { name: "payer_name_mismatch", blurb: "The bank calls the payer something the ledger never does." },
  { name: "unrelated_credit", blurb: "A credit that belongs to nothing in this batch at all." },
  { name: "unmatchable", blurb: "A record with no counterpart anywhere, by design." },
];

/** The share of a generated corpus seeded as a difficulty rather than a clean
 *  tie-out. Mirrors HARD_SHARE in backend/datagen/generator.py. */
export const HARD_SHARE = 0.15;
