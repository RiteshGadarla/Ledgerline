import Link from "next/link";

/** One address for the attribution, so every footer says the same thing. */
export const AUTHOR = "Gadarla Ritesh";
export const AUTHOR_URL = "https://www.gadarlaritesh.me/";
export const TRACK = "Razorpay Buildathon · Track 04 — AI Finance Controller";

/**
 * The credit that closes every surface. It renders as the trailing segments
 * of the status strip -- same monospace terminal line as the run state and
 * the engine version -- so the build's provenance is visible on every page
 * without a second piece of chrome to carry it. `ml-auto` pins the pair to
 * the right of whatever segments the surface put in front of it.
 */
export function Credit() {
  return (
    <>
      <span className="strip-seg ml-auto hidden sm:flex" title={TRACK}>
        RAZORPAY BUILDATHON · <b>TRACK 04</b>
      </span>
      <span className="strip-seg sm:ml-0 ml-auto">
        MADE BY{" "}
        <a
          href={AUTHOR_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-[color:var(--rail-hi)] underline-offset-2 hover:text-white hover:underline"
        >
          GADARLA RITESH
        </a>
      </span>
    </>
  );
}

/**
 * The same credit as running text, for the surfaces that draw no status
 * strip: the two auth screens, which are a form on a paper ground rather
 * than an instrument frame.
 */
export function CreditLine({ className = "" }: { className?: string }) {
  return (
    <p className={"text-[12.5px] leading-[1.7] text-faint " + className}>
      <span className="block">
        Built for the <span className="font-medium text-muted">Razorpay Buildathon</span> ·{" "}
        <span className="mono text-[11.5px] tracking-[0.06em]">TRACK 04</span> · AI Finance
        Controller
      </span>
      <span className="block">
        Made by{" "}
        <a href={AUTHOR_URL} target="_blank" rel="noopener noreferrer" className="link font-medium">
          {AUTHOR}
        </a>
        {" · "}
        <Link href="/" className="link">
          Ledgerline
        </Link>
      </span>
    </p>
  );
}
