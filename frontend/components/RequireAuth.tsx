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
      <p className="p-6 text-sm text-muted" role="status">
        {session.status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
      </p>
    );
  }

  return <>{children}</>;
}
