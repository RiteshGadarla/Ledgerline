"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { api } from "@/lib/api/client";

const COPY = {
  login: {
    eyebrow: "Welcome back",
    heading: "Sign in to Ledgerline",
    sub: "Pick up where the last run left off.",
    submit: "Sign in",
    pending: "Signing in…",
    footer: "No account yet?",
    footerHref: "/register",
    footerLink: "Create one",
  },
  register: {
    eyebrow: "Get started",
    heading: "Create your account",
    sub: "One account holds your datasets, your runs and every decision recorded against them.",
    submit: "Create account",
    pending: "Creating account…",
    footer: "Already have an account?",
    footerHref: "/login",
    footerLink: "Sign in",
  },
} as const;

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const copy = COPY[mode];
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
    <div className="rise-in w-full max-w-[380px]">
      <Link
        href="/"
        className="inline-flex items-center gap-2.5 lg:hidden"
        aria-label="Ledgerline home"
      >
        <Logo size={28} />
        <span className="text-[16.5px] font-semibold tracking-[-0.01em]">Ledgerline</span>
      </Link>

      <p className="legend mt-8 lg:mt-0">{copy.eyebrow}</p>
      <h1 className="mt-3 text-[clamp(1.55rem,2.2vw,2rem)] font-semibold leading-[1.1] tracking-[-0.03em]">
        {copy.heading}
      </h1>
      <p className="mt-2.5 text-[15px] leading-relaxed text-muted">{copy.sub}</p>

      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4" noValidate>
        <div className="flex flex-col gap-2">
          <label className="legend" htmlFor="auth-username">
            Username
          </label>
          <input
            id="auth-username"
            className="field"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            placeholder="e.g. rahul.sharma"
            autoFocus
          />
        </div>

        <div className="flex flex-col gap-2">
          <label className="legend" htmlFor="auth-password">
            Password
          </label>
          <input
            id="auth-password"
            className="field"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            placeholder="••••••••••••"
          />
        </div>

        {error && (
          <p
            role="alert"
            className="rounded-[3px] border border-signal bg-signal-bg px-3 py-2 text-sm text-signal"
          >
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting} className="btn btn-primary btn-lg mt-1 w-full">
          {submitting ? copy.pending : copy.submit}
        </button>
      </form>

      <div className="mt-7 flex items-center gap-3">
        <span aria-hidden className="h-px flex-1 bg-hairline" />
        <span className="legend">{copy.footer}</span>
        <span aria-hidden className="h-px flex-1 bg-hairline" />
      </div>
      <Link href={copy.footerHref} className="btn mt-3.5 w-full">
        {copy.footerLink}
      </Link>
    </div>
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
