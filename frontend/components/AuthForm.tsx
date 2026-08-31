"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api/client";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const path = mode === "login" ? "/auth/login" : "/auth/register";
    const { error: apiError } = await api.POST(path, { body: { username, password } });

    setSubmitting(false);
    if (apiError) {
      setError(describeAuthError(apiError, mode));
      return;
    }
    router.push("/run");
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto flex max-w-sm flex-col gap-4" noValidate>
      <h1 className="text-lg font-semibold">{mode === "login" ? "Sign in" : "Create an account"}</h1>

      <label className="flex flex-col gap-1 text-sm">
        Username
        <input
          className="border border-hairline px-3 py-2 text-sm"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        Password
        <input
          className="border border-hairline px-3 py-2 text-sm"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
        />
      </label>

      {error && (
        <p role="alert" className="text-sm text-signal">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="border border-foreground bg-foreground px-3 py-2 text-sm font-medium text-background disabled:opacity-50"
      >
        {submitting ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
      </button>
    </form>
  );
}

function describeAuthError(error: unknown, mode: "login" | "register"): string {
  const detail =
    typeof error === "object" && error !== null && "detail" in error
      ? String((error as { detail?: unknown }).detail)
      : null;
  if (detail) return detail;
  return mode === "login" ? "Username or password is incorrect." : "Could not create that account.";
}
