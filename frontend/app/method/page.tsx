const SECTIONS = [
  {
    title: "How matching works",
    body: "Four deterministic passes run in order: P1 ties a bank credit to a settlement by amount and UTR, P2 recomputes a settlement's payout in integer paise from its batch of payments, P3 links an invoice to a payment by an exact invoice reference, and P4 solves a bounded subset-sum for split payments. A single verifier independently re-checks every proposed match's arithmetic before it is ever recorded -- nothing is written as a match without passing that check, whether it came from a deterministic pass or from LLM-assisted triage.",
  },
  {
    title: "How metrics are computed",
    body: "Auto rate, assist rate, and open rate are computed over payments: the share that landed in a deterministic (auto) group, an LLM-assisted group, or an exception. Precision and recall only render when a run has an accompanying truth file (the seeded demo corpus); a live run without ground truth reports auto/assist/open rates and false-match counts only.",
  },
  {
    title: "The truth-file approach",
    body: "The demo corpus generator produces a synthetic set of invoices, payments, settlements, and bank lines alongside a truth file recording which records truly belong together and which are genuinely unmatchable by design (duplicates, unrelated credits, chargebacks). Scoring a run against that truth file is what makes precision and recall meaningful for the demo corpus specifically -- it is not available for your own uploaded data.",
  },
  {
    title: "Known failure modes",
    body: "LLM-assisted triage can degrade (rate limit, quota, or a malformed response) without failing the run: a degraded run reports assist_rate = 0 and llm_degraded = true, with every item that would have gone to triage left as a typed exception instead. PDF bank-statement extraction has only been validated against synthetic fixtures generated in this repository; an unusual real-world layout may not extract cleanly.",
  },
  {
    title: "Out of scope",
    body: "Bring-your-own-dataset runs, the mutation-testing engine, and the ask agent are not yet implemented end to end. Cash position figures project over the corpus's own settlement window, not the calendar date you're viewing this page on.",
  },
];

export default function MethodPage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-lg font-semibold">Method</h1>
      <div className="mt-6 flex flex-col gap-6">
        {SECTIONS.map((section) => (
          <section key={section.title}>
            <h2 className="text-sm font-semibold">{section.title}</h2>
            <p className="mt-1 text-sm text-muted">{section.body}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
