"use client";

import { useSession } from "@/lib/session";

/**
 * Who is signed in, sitting at the right of a surface's context bar. The rail
 * is 64px of icons at every width and has no room to name anyone, so the
 * identity lives here, on every signed-in surface, in the same slot.
 */
export function UserBadge() {
  const session = useSession();

  if (session.status !== "authenticated") {
    return null;
  }

  return (
    <span className="flex items-center gap-2">
      <span
        aria-hidden
        className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-[3px] bg-accent font-mono text-[12.5px] font-semibold uppercase text-[color:var(--on-accent)]"
      >
        {session.user.username.slice(0, 2)}
      </span>
      <span className="max-w-[16ch] truncate text-[14.5px] font-medium">{session.user.username}</span>
    </span>
  );
}
