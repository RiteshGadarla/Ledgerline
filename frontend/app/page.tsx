"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useSession } from "@/lib/session";

export default function HomePage() {
  const session = useSession();
  const router = useRouter();

  useEffect(() => {
    if (session.status === "authenticated") router.replace("/run");
    if (session.status === "anonymous") router.replace("/login");
  }, [session.status, router]);

  return (
    <p className="p-6 text-sm text-muted" role="status">
      Loading…
    </p>
  );
}
