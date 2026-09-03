"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { StatusStrip } from "@/components/StatusStrip";
import { useSession } from "@/lib/session";

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const session = useSession();
  const router = useRouter();

  useEffect(() => {
    if (session.status === "anonymous") {
      router.replace("/login");
    }
  }, [session.status, router]);

  if (session.status !== "authenticated") {
    return (
      // The strip goes on even here, so the gate reads as the same instrument
      // as the surface behind it rather than a bare loading screen.
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-1 items-center gap-3 p-8 text-sm text-muted" role="status">
          <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
          {session.status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
        </div>
        <StatusStrip segments={[]} />
      </div>
    );
  }

  return <>{children}</>;
}
