"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
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
      <div className="flex flex-1 items-center gap-3 p-8 text-sm text-muted" role="status">
        <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
        {session.status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
      </div>
    );
  }

  return <>{children}</>;
}
