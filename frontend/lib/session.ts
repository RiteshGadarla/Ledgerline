"use client";

import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { components } from "./api/client";

type UserOut = components["schemas"]["UserOut"];

export type SessionState =
  | { status: "loading" }
  | { status: "authenticated"; user: UserOut }
  | { status: "anonymous" };

export function useSession(): SessionState {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api.GET("/auth/me").then(({ data, error }) => {
      if (cancelled) return;
      if (error || !data) {
        setState({ status: "anonymous" });
      } else {
        setState({ status: "authenticated", user: data });
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
