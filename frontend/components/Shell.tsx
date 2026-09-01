"use client";

import { usePathname } from "next/navigation";
import { Lyra } from "@/components/Lyra";
import { Rail } from "@/components/Rail";

// Routes that own their full-bleed canvas: the landing page and the two auth
// screens draw their own chrome, so the rail and the content well that every
// signed-in surface sits in are suppressed there.
const BARE_ROUTES = ["/", "/login", "/register"];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (BARE_ROUTES.includes(pathname)) return <>{children}</>;

  // A fixed instrument frame: the rail and the status strip never move, and
  // the stage inside each surface is the only thing that scrolls. `dvh`
  // rather than `vh` so a mobile browser's collapsing toolbar doesn't push
  // the tab bar out of reach.
  return (
    <div className="flex h-dvh flex-col overflow-hidden md:flex-row">
      <Rail />
      <div className="order-1 flex min-h-0 min-w-0 flex-1 flex-col md:order-none">{children}</div>
      {/* Self-gating: Lyra renders only on a run's own surfaces. */}
      <Lyra />
    </div>
  );
}
