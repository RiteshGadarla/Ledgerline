"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { useSession } from "@/lib/session";

const LINKS = [
  { href: "/run", label: "Run" },
  { href: "/data", label: "Data" },
  { href: "/method", label: "Method" },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const session = useSession();

  async function signOut() {
    await api.POST("/auth/logout");
    router.push("/login");
  }

  return (
    <header className="border-b border-hairline">
      <nav
        aria-label="Primary"
        className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3 text-sm"
      >
        <span className="font-semibold tracking-tight">Ledgerline</span>
        <ul className="flex gap-4">
          {LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                aria-current={pathname.startsWith(link.href) ? "page" : undefined}
                className={
                  pathname.startsWith(link.href)
                    ? "font-medium text-foreground"
                    : "text-muted hover:text-foreground"
                }
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="ml-auto flex items-center gap-4">
          {session.status === "authenticated" && (
            <>
              <span className="text-muted">{session.user.username}</span>
              <button type="button" onClick={signOut} className="text-muted hover:text-foreground">
                Sign out
              </button>
            </>
          )}
        </div>
      </nav>
    </header>
  );
}
