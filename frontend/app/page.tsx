"use client";

import Image from "next/image";
import Link from "next/link";
import { Credit } from "@/components/Credit";
import { Logo } from "@/components/Logo";
import { Ticks } from "@/components/Stat";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useSession } from "@/lib/session";

const CHAIN = [
  { role: "Invoice", count: "1,400", detail: "raised in the ledger", tied: "1,362 TIED", broken: "38 UNMATCHED" },
  { role: "Payment", count: "1,362", detail: "captured by the gateway", tied: "1,344 TIED", broken: "18 UNSETTLED" },
  { role: "Settlement", count: "120", detail: "batches paid out", tied: "118 TIED", broken: "2 NO BANK LINE" },
  { role: "Bank line", count: "118", detail: "credits on the statement", tied: null, broken: null },
];

/**
 * The passes as the engine actually runs them, which is not four abreast: P1
 * and P3 are the two linking passes `match()` wires, P2 is the recompute the
 * verifier performs on every proposal whoever made it, and P4 is implemented
 * and unit tested but not yet enabled. A landing page a reader can falsify by
 * opening `pipeline.py` costs more than the fourth card is worth, so the card
 * carries its own status instead.
 */
const PASSES: { id: string; title: string; body: string; status?: string }[] = [
  {
    id: "P1",
    title: "Bank → settlement",
    body: "Ties a bank credit to a settlement on UTR, an exact amount and a date window. A credit that posted six weeks late is a break, not a match.",
  },
  {
    id: "P2",
    title: "Payout recompute",
    body: "Rebuilds a settlement's payout from its own batch of payments, in integer paise, inside the verifier. Float never touches an amount.",
  },
  {
    id: "P3",
    title: "Invoice → payment",
    body: "Links an invoice to a payment on an exact reference, falling back to a unique amount in-window, so the chain runs end to end.",
  },
  {
    id: "P4",
    title: "Split payments",
    body: "A bounded subset-sum for the one credit that covers many invoices. Implemented and unit tested, but not wired into a run yet.",
    status: "Not enabled",
  },
];

const PRINCIPLES = [
  {
    title: "Nothing is matched on trust",
    body: "Every proposed match, deterministic or assisted, is re-checked by an independent verifier before it is recorded. A proposal that fails its check becomes a typed exception, not a match.",
  },
  {
    title: "Degradation is visible, not silent",
    body: "If assisted triage hits a rate limit or returns something malformed, the run still finishes: it reports assist rate zero, flags itself degraded, and files every affected item as an exception.",
  },
  {
    title: "The same input gives the same output",
    body: "Runs are seeded and hashed. Re-run a seed and size and you get the same output hash, which is what makes a reconciliation defensible after the fact.",
  },
];

/* The figures on this page are a labelled sample of one run, not a live
   reading; the landing page fetches nothing. */
const SAMPLE_SPEC = [
  { k: "Records", v: "1,400", tone: "" },
  { k: "Value traced", v: "₹4.12 Cr", tone: "" },
  { k: "Wall clock", v: "1.13 s", tone: "" },
  { k: "False matches", v: "0", tone: "text-positive" },
];

const SAMPLE_FACES = [
  { label: "Match rate (auto)", value: "94.2%", tone: "", ticks: { on: 38 } },
  { label: "Assist rate", value: "4.1%", tone: "", ticks: { on: 2 } },
  { label: "Open exceptions", value: "17", tone: "", ticks: { on: 39, risk: 1 } },
  { label: "Rupees at risk", value: "₹2,48,310", tone: "text-signal", ticks: { on: 36, risk: 4 } },
];

export default function LandingPage() {
  const session = useSession();
  const signedIn = session.status === "authenticated";

  return (
    <div className="flex min-h-screen flex-col">
      {/* An instrument masthead carrying the engine's spec line, not a
          blurred glass bar floating over the content. */}
      <header className="flex min-h-[46px] flex-wrap items-center gap-x-[clamp(0.875rem,2vw,1.875rem)] gap-y-2 border-b border-rail-line bg-rail px-[clamp(1rem,2.4vw,2.5rem)] py-2">
        <Link href="/" className="flex items-center gap-2.5 text-white" aria-label="Ledgerline home">
          <Logo size={23} className="text-white" />
          <span className="text-[15.5px] font-semibold tracking-[-0.01em]">Ledgerline</span>
        </Link>

        <div className="mono hidden items-center text-[11px] tracking-[0.14em] text-rail-ink md:flex">
          <span>ENGINE v0.12</span>
          <span className="ml-3.5 border-l border-rail-line pl-3.5">DETERMINISTIC</span>
          <span className="ml-3.5 border-l border-rail-line pl-3.5">VERIFIER-GATED</span>
        </div>

        <nav className="ml-auto flex items-center gap-1.5" aria-label="Account">
          <ThemeToggle className="!border-transparent !bg-transparent !text-rail-ink hover:!border-rail-line hover:!text-white" />
          {signedIn ? (
            <Link
              href="/run"
              className="inline-flex min-h-[34px] items-center rounded-[3px] bg-[color:var(--readout-hi)] px-3.5 text-[14.5px] font-semibold text-[#04211d] hover:bg-white"
            >
              Open console
            </Link>
          ) : (
            <>
              <Link
                href="/login"
                className="inline-flex min-h-[34px] items-center rounded-[3px] px-3 text-[14.5px] text-rail-ink hover:text-white"
              >
                Sign in
              </Link>
              <Link
                href="/register"
                className="inline-flex min-h-[34px] items-center rounded-[3px] bg-[color:var(--readout-hi)] px-3.5 text-[14.5px] font-semibold text-[#04211d] hover:bg-white"
              >
                Create an account
              </Link>
            </>
          )}
        </nav>
      </header>

      <main className="flex-1">
        {/* ------------------------------------------------------------ hero */}
        <div className="mx-auto w-full max-w-[1560px] px-[clamp(1rem,2.4vw,2.5rem)] py-[clamp(2.75rem,4.6vw,4.875rem)]">
          <div className="grid items-center gap-[clamp(1.625rem,3.2vw,3.375rem)] lg:[grid-template-columns:minmax(0,1.06fr)_minmax(0,0.94fr)]">
            <div className="rise-in">
              <p className="flex items-center gap-2.5">
                <span aria-hidden className="h-1.5 w-1.5 bg-readout-hi" />
                <span className="legend">For Indian payment gateways</span>
              </p>

              <h1 className="mt-5 text-[clamp(2.25rem,4.3vw,3.75rem)] font-semibold leading-[1.02] tracking-[-0.036em] text-balance">
                Close the books.
                <br />
                Show the work.
              </h1>

              <p className="mt-6 max-w-[56ch] text-[clamp(0.9rem,1.1vw,1.03rem)] leading-[1.62] text-pretty text-muted">
                Ledgerline walks every rupee from invoice to payment to settlement to the line on
                your bank statement. Two deterministic passes do the matching, an independent
                verifier recomputes every amount in integer paise, and anything that won&apos;t tie
                out arrives as a typed exception with its evidence attached.
              </p>

              <div className="mt-7 flex flex-wrap gap-2.5">
                <Link href={signedIn ? "/run" : "/register"} className="btn btn-primary btn-lg">
                  {signedIn ? "Go to your console" : "Create an account"}
                </Link>
              </div>
            </div>

            {/* An instrument face, not a stock screenshot. */}
            <div className="rise-in panel overflow-hidden">
              <div className="panel-head">
                <span className="legend legend-hi">Run</span>
                <span className="mono text-[13.5px] font-medium">7f3c9a21</span>
                <span className="chip chip-tied ml-auto">
                  <span className="dot" />
                  Complete
                </span>
                <span className="chip">Sample</span>
              </div>

              <div className="grid grid-cols-2 gap-px bg-hairline">
                {SAMPLE_FACES.map((face) => (
                  <div key={face.label} className="bg-surface p-4">
                    <span className="legend">{face.label}</span>
                    <p
                      className={
                        "mono mt-2.5 text-[clamp(1.3rem,1.7vw,1.6rem)] font-medium tracking-[-0.025em] " +
                        face.tone
                      }
                    >
                      {face.value}
                    </p>
                    <Ticks on={face.ticks.on} risk={face.ticks.risk} />
                  </div>
                ))}
              </div>

              <div className="strip">
                <span className="strip-seg">
                  <span aria-hidden className="dot" style={{ color: "var(--positive)" }} />
                  <b>VERIFIER OK</b>
                </span>
                <span className="strip-seg">P2 stl_0442 · 38 payments</span>
              </div>
            </div>
          </div>

          {/* The hero's closing line, spanning the full well: what one run
              measured on the left, who built it on the right, so the rule
              under the grid carries both instead of leaving the corner empty. */}
          <div className="mt-[clamp(1.5rem,2.8vw,2.5rem)] flex flex-wrap items-center justify-between gap-x-[clamp(1.5rem,3vw,3rem)] gap-y-6 border-t border-hairline pt-4">
            <div className="flex flex-wrap">
              {SAMPLE_SPEC.map((item) => (
                <span
                  key={item.k}
                  className="mr-[clamp(1rem,2vw,2rem)] flex flex-col gap-1.5 border-r border-hairline pr-[clamp(1rem,2vw,2rem)] last:mr-0 last:border-r-0 last:pr-0"
                >
                  <span className="legend">{item.k}</span>
                  <span className={"mono text-[15.5px] " + item.tone}>{item.v}</span>
                </span>
              ))}
            </div>

            <div className="flex items-center gap-3.5">
              {/* The asset is a 256px square whose artwork occupies one 55px
                  band; the window crops to that band, and the white plate keeps
                  the navy wordmark legible in the dark theme too. */}
              <span className="brand-plate shrink-0">
                <span className="relative block h-[26px] w-[120px]">
                  <Image
                    src="/razorpayBRAND.webp"
                    alt="Razorpay"
                    fill
                    sizes="120px"
                    className="object-cover object-center"
                  />
                </span>
              </span>
              <p className="text-[13.5px] leading-[1.6] text-muted">
                <span className="block">
                  Built for the <span className="font-medium text-foreground">Razorpay Buildathon</span>
                </span>
                <span className="block">
                  <span className="mono text-[12.5px] tracking-[0.06em]">TRACK 04</span> · AI Finance
                  Controller
                </span>
                <span className="block">
                  by{" "}
                  <a
                    href="https://www.gadarlaritesh.me/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="link font-medium"
                  >
                    Gadarla Ritesh
                  </a>
                </span>
              </p>
            </div>
          </div>
        </div>

        <div className="mx-auto w-full max-w-[1560px] px-[clamp(1rem,2.4vw,2.5rem)]">
          {/* ------------------------------------------------------ 01 chain */}
          <section className="border-t border-hard py-[clamp(2.375rem,3.8vw,3.875rem)]">
            <p className="mono text-[12px] tracking-[0.18em] text-accent">01 · THE CHAIN</p>
            <div className="mt-4 flex flex-wrap items-end justify-between gap-5">
              <h2 className="max-w-[20ch] text-[clamp(1.44rem,2.3vw,2.06rem)] font-semibold leading-[1.14] tracking-[-0.026em] text-balance">
                Four sources, one story per rupee.
              </h2>
              <p className="max-w-[42ch] text-[16px] leading-relaxed text-pretty text-muted">
                Reconciliation breaks where the sources disagree. Keeping all four in one chain gives
                a break an address instead of a hunt.
              </p>
            </div>

            <div className="graph-ground panel mt-8 p-[clamp(1rem,2.6vw,2.5rem)]">
              <div className="flex flex-col items-stretch md:flex-row">
                {CHAIN.map((node) => (
                  <div key={node.role} className="contents">
                    <div className="md:w-[clamp(8.125rem,11vw,10.5rem)] md:shrink-0">
                      <div className="panel border-hairline-strong p-3.5">
                        <span className="legend">{node.role}</span>
                        <p className="mono mt-2 text-[1.44rem] font-medium tracking-[-0.02em]">
                          {node.count}
                        </p>
                        <p className="mt-1.5 text-[13px] text-muted">{node.detail}</p>
                      </div>
                    </div>

                    {node.tied && (
                      <div className="flex min-w-0 flex-1 flex-row items-center gap-3 px-1 py-2 md:flex-col md:justify-center md:px-[clamp(0.5rem,1vw,1rem)] md:py-0">
                        <span className="mono text-[11.5px] tracking-[0.06em] text-positive">
                          {node.tied}
                        </span>
                        <span aria-hidden className="h-px flex-1 bg-hairline-strong md:my-2 md:w-full md:flex-none" />
                        <span className="mono text-[11.5px] tracking-[0.06em] text-signal">
                          {node.broken}
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ----------------------------------------------------- 02 engine */}
          <section className="border-t border-hard py-[clamp(2.375rem,3.8vw,3.875rem)]">
            <p className="mono text-[12px] tracking-[0.18em] text-accent">02 · THE ENGINE</p>
            <div className="mt-4 flex flex-wrap items-end justify-between gap-5">
              <h2 className="max-w-[22ch] text-[clamp(1.44rem,2.3vw,2.06rem)] font-semibold leading-[1.14] tracking-[-0.026em] text-balance">
                Deterministic first. Assisted only where it has to be.
              </h2>
              <p className="max-w-[42ch] text-[16px] leading-relaxed text-pretty text-muted">
                Each pass is arithmetic you could check by hand. A model is brought in to triage what
                is left over, never to decide that two records match.
              </p>
            </div>

            <div className="mt-9 grid [grid-template-columns:repeat(auto-fit,minmax(13.125rem,1fr))]">
              {PASSES.map((pass) => (
                <div
                  key={pass.id}
                  className="border-l border-hairline px-[clamp(0.8125rem,1.5vw,1.5rem)] first:border-l-0 first:pl-0"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="mono text-[12.5px] font-semibold tracking-[0.1em] text-accent">
                      {pass.id}
                    </span>
                    {pass.status && (
                      <span className="mono text-[10.5px] uppercase tracking-[0.08em] text-faint">
                        {pass.status}
                      </span>
                    )}
                  </span>
                  <h3 className="mt-3 text-[16.5px] font-semibold tracking-[-0.01em]">{pass.title}</h3>
                  <p className="mt-2.5 text-[14px] leading-relaxed text-muted">{pass.body}</p>
                </div>
              ))}
            </div>

            <div className="panel mt-9 flex flex-wrap items-center gap-x-5 gap-y-4 border-l-2 border-l-readout-hi p-5">
              <div className="min-w-0 flex-1 basis-80">
                <span className="legend legend-hi">The verifier</span>
                <p className="mt-2 max-w-[72ch] text-[14.5px] leading-relaxed text-muted">
                  Whatever proposes a match, a pass or a model, the same independent check
                  re-derives the arithmetic in integer paise before anything is written. A proposal
                  that fails becomes a typed exception carrying the check it failed.
                </p>
              </div>
              <pre className="mono whitespace-pre-line border-hairline text-[12.5px] leading-[1.9] text-muted sm:border-l sm:pl-5">
{`proposal   stl_0451 ← 32 pay
recompute  ₹3,98,760.00
declared   ₹4,40,660.00
verdict    FAILED → exception`}
              </pre>
            </div>
          </section>

          {/* -------------------------------------------------- 03 guarantee */}
          <section className="border-t border-hard py-[clamp(2.375rem,3.8vw,3.875rem)]">
            <p className="mono text-[12px] tracking-[0.18em] text-accent">03 · THE GUARANTEE</p>
            <div className="mt-7 grid gap-[clamp(1.125rem,2.2vw,2.375rem)] [grid-template-columns:repeat(auto-fit,minmax(14.375rem,1fr))]">
              {PRINCIPLES.map((principle) => (
                <div key={principle.title}>
                  <span aria-hidden className="block h-0.5 w-8 bg-readout-hi" />
                  <h3 className="mt-4 text-[17.5px] font-semibold tracking-[-0.015em]">
                    {principle.title}
                  </h3>
                  <p className="mt-2.5 text-[14.5px] leading-relaxed text-muted">{principle.body}</p>
                </div>
              ))}
            </div>
          </section>

          {/* ----------------------------------------------------- the ask */}
          <section className="pb-[clamp(2.5rem,4vw,4rem)]">
            <div className="rounded-[3px] bg-rail p-[clamp(1.75rem,3.2vw,3.125rem)] text-white">
              <div className="flex flex-wrap items-end justify-between gap-7">
                <div className="min-w-0 flex-1 basis-[26rem]">
                  <p className="mono text-[12px] tracking-[0.18em] text-[color:var(--readout-hi)]">
                    START HERE
                  </p>
                  <h2 className="mt-4 max-w-[18ch] text-[clamp(1.44rem,2.3vw,2.06rem)] font-semibold leading-[1.14] tracking-[-0.026em]">
                    Your first run takes one file set.
                  </h2>
                  <p className="mt-3.5 max-w-[52ch] text-[16px] leading-relaxed text-rail-ink">
                    Upload your ledger, gateway, settlement and bank files, or generate a synthetic
                    corpus with a known truth file and score against it.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2.5">
                  <Link
                    href={signedIn ? "/run" : "/register"}
                    className="inline-flex min-h-11 items-center rounded-[3px] bg-[color:var(--readout-hi)] px-5 text-sm font-semibold text-[#04211d] hover:bg-white"
                  >
                    {signedIn ? "Go to your console" : "Create an account"}
                  </Link>
                </div>
              </div>
            </div>
          </section>

        </div>
      </main>

      {/* This page scrolls as a document rather than sitting in the app's
          fixed frame, so the strip is stuck to the foot of the viewport: the
          credit stays on screen the whole way down instead of waiting at the
          end of the last section. */}
      <div className="strip sticky bottom-0 z-20 mt-auto">
        <span className="strip-seg">
          <b>LEDGERLINE</b>
        </span>
        <span className="strip-seg">
          Precision and recall render only against a corpus with a truth file.
        </span>
        <span className="strip-seg">
          ENGINE <b>v0.12</b>
        </span>
        <Credit />
      </div>
    </div>
  );
}
