"use client";

export const THEME_KEY = "ledgerline-theme";

/**
 * Light is the product's default, so only an explicit choice of dark is ever
 * stored and stamped on <html>. The button holds both glyphs and both labels
 * and lets CSS pick one by `data-theme`, so the component needs no state of
 * its own, server and client markup stay identical (no hydration mismatch),
 * and there is no flash of the wrong icon on load. The hidden half is
 * `display: none`, which also keeps it out of the accessible name.
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  function toggle() {
    const root = document.documentElement;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    if (next === "dark") {
      root.dataset.theme = "dark";
    } else {
      delete root.dataset.theme;
    }
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private mode or blocked storage: the choice just won't persist.
    }
  }

  return (
    <button type="button" onClick={toggle} title="Switch colour theme" className={"btn btn-icon " + className}>
      <span className="sr-only theme-icon-moon">Switch to dark theme</span>
      <span className="sr-only theme-icon-sun">Switch to light theme</span>

      {/* moon, offered while light is live */}
      <svg
        className="theme-icon-moon"
        width="17"
        height="17"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>

      {/* sun, offered while dark is live */}
      <svg
        className="theme-icon-sun"
        width="17"
        height="17"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    </button>
  );
}
