"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api, clearApiCache } from "@/lib/api/client";
import { useSession } from "@/lib/session";

/**
 * The bezel. There is no top navbar anywhere in the app: primary navigation
 * is this rail, dark in both themes because it is chrome framing a readout
 * rather than content. It stays 64px of icons at every width; with two
 * destinations whose names are one short word, the icon and its caption
 * already say everything a 208px drawer would, and the width is better spent
 * on the readout. Below `md` it lies down as a bottom tab bar.
 */

const LINKS = [
  {
    href: "/run",
    label: "Run",
    icon: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  },
  {
    href: "/data",
    label: "Data",
    icon: (
      <>
        <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
        <path d="m3 12.4 9 4.5 9-4.5" />
        <path d="m3 16.9 9 4.5 9-4.5" />
      </>
    ),
  },
];

function Icon({ children }: { children: React.ReactNode }) {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

// /runs/[id]/* is a child of the Run surface, so it keeps Run highlighted.
function isActive(pathname: string, href: string): boolean {
  if (href === "/run") return pathname === "/run" || pathname.startsWith("/runs/");
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Rail() {
  const pathname = usePathname();
  const router = useRouter();
  const session = useSession();

  async function signOut() {
    await api.POST("/auth/logout");
    // Nothing this session read stays in memory for the next one. The cached
    // bodies are already tenant-safe -- a different user's row hashes to a
    // different ETag, so the server answers 200 with theirs -- but there is
    // no reason to hold one account's data while another is signing in.
    clearApiCache();
    router.push("/login");
  }

  return (
    <nav
      aria-label="Primary"
      className="
        order-2 flex w-full shrink-0 flex-row items-stretch border-t border-rail-line bg-rail
        md:order-none md:w-16 md:flex-col md:items-stretch md:border-t-0 md:border-r md:py-3
      "
    >
      <Link
        href="/"
        aria-label="Ledgerline home"
        className="hidden shrink-0 justify-center py-1 text-rail-hi md:flex"
      >
        <Logo size={28} className="text-white" />
      </Link>

      {/* Two destinations, centred in the space between the mark and the
          controls rather than stacked under the mark. */}
      <ul className="flex flex-1 flex-row md:flex-col md:justify-center md:gap-1.5 md:px-2">
        {LINKS.map((link) => {
          const active = isActive(pathname, link.href);
          return (
            <li key={link.href} className="flex-1 md:flex-none">
              <Link
                href={link.href}
                data-tour={`rail-${link.label.toLowerCase()}`}
                aria-current={active ? "page" : undefined}
                className={
                  "relative flex min-h-[56px] flex-col items-center justify-center gap-1 rounded-[3px] px-1 py-1.5 transition-colors " +
                  "md:min-h-[58px] " +
                  (active
                    ? "bg-white/[0.07] text-white"
                    : "text-rail-ink hover:bg-white/5 hover:text-white")
                }
              >
                {/* the active tick, on the leading edge in every orientation */}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-[22%] top-0 h-0.5 rounded-b-sm bg-readout-hi md:inset-x-auto md:inset-y-3 md:left-0 md:h-auto md:w-0.5 md:rounded-r-sm"
                  />
                )}
                <Icon>{link.icon}</Icon>
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.09em]">
                  {link.label}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      {/* Two controls, same 36px square, so the foot of the rail reads as one
          pair rather than a stack of mismatched widths. The signed-in user is
          named in each surface's context bar, not here. */}
      <div className="hidden shrink-0 flex-col items-center gap-2 px-2 md:flex">
        <span aria-hidden className="h-px w-7 bg-rail-line" />

        <ThemeToggle className="!h-9 !min-h-9 !w-9 !border-rail-line !bg-transparent !text-rail-ink hover:!border-rail-ink hover:!text-white" />

        {session.status === "authenticated" && (
          <button
            type="button"
            onClick={signOut}
            title="Sign out"
            aria-label="Sign out"
            className="btn btn-icon !h-9 !min-h-9 !w-9 !border-rail-line !bg-transparent !text-rail-ink hover:!border-rail-ink hover:!text-white"
          >
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
              <path d="M10 17 5 12l5-5" />
              <path d="M5 12h11" />
            </svg>
          </button>
        )}
      </div>

    </nav>
  );
}
