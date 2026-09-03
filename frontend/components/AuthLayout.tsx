import Link from "next/link";
import { CreditLine } from "@/components/Credit";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

const PROOF = [
  {
    k: "VERIFIER",
    v: "Every proposed match is re-derived in integer paise before it is recorded.",
  },
  {
    k: "DETERMINISM",
    v: "The same seed and size reproduce the same output hash, run after run.",
  },
  {
    k: "TENANCY",
    v: "Datasets, runs and decisions never cross an account boundary.",
  },
];

const TRACE = `run   7f3c9a21
rec   1,400 · 1.13 s
auto  94.2% · assist 4.1%
false matches 0
hash  9ac1f0…3b2e`;

/**
 * The auth screens split like the app itself: the dark bezel on one side
 * carrying what the product guarantees, the paper form on the other. The
 * panel collapses below `lg`, where the form takes the full width.
 */
export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:[grid-template-columns:minmax(0,0.85fr)_minmax(0,1.15fr)]">
      <aside className="hidden flex-col bg-rail p-[clamp(1.75rem,3vw,2.875rem)] text-white lg:flex">
        <Link href="/" className="inline-flex items-center gap-2.5" aria-label="Ledgerline home">
          <Logo size={25} className="text-white" />
          <span className="text-[16.5px] font-semibold tracking-[-0.01em]">Ledgerline</span>
        </Link>

        <div className="mt-auto">
          <p className="mono text-[12px] tracking-[0.16em] text-[color:var(--readout-hi)]">
            THE GUARANTEE
          </p>
          <h2 className="mt-4 max-w-[20ch] text-[clamp(1.3rem,1.9vw,1.75rem)] font-semibold leading-[1.2] tracking-[-0.025em]">
            Every figure on the scoreboard was re-derived before it was written.
          </h2>

          <dl className="mt-6 border-t border-rail-line">
            {PROOF.map((item) => (
              <div key={item.k} className="flex items-baseline gap-3.5 border-b border-rail-line py-3">
                <dt className="mono w-24 shrink-0 text-[11.5px] tracking-[0.12em] text-rail-ink">
                  {item.k}
                </dt>
                <dd className="text-[14px] leading-snug text-[#c8d1dc]">{item.v}</dd>
              </div>
            ))}
          </dl>

          <p className="mono mt-6 whitespace-pre-line text-[12px] leading-relaxed text-rail-ink">
            {TRACE}
          </p>
        </div>
      </aside>

      <div className="relative flex flex-col items-center justify-center gap-8 bg-surface p-[clamp(1.5rem,3vw,3rem)]">
        <ThemeToggle className="absolute right-5 top-5" />
        {children}
        {/* These two screens draw no status strip, so the credit the rest of
            the app carries along its bottom line runs as text here instead. */}
        <CreditLine className="text-center" />
      </div>
    </div>
  );
}
