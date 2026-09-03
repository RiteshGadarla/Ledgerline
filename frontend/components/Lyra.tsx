"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { useSession } from "@/lib/session";

/**
 * Lyra: the settlement Q&A agent.
 *
 * The `/ask` endpoint is grounded on exactly one run, and there is nothing
 * for Lyra to answer about anywhere else, so she appears only on a run's own
 * surfaces (`/runs/<id>/*`) and grounds on the run you are looking at. A
 * handle on the Run console or the Data library would be an invitation to a
 * conversation the agent cannot have.
 *
 * Answers come from the same verified figures the surfaces render; the model
 * reads them through tool calls and does no arithmetic of its own.
 */

type Message = { from: "lyra"; text: string; degraded?: boolean } | { from: "you"; text: string };

/**
 * Reads the `/ask/stream` SSE frames, calling `onEvent` for each.
 *
 * EventSource can't POST, and the question belongs in a body rather than a
 * URL, so the stream is read off the fetch response directly. Frames are
 * separated by a blank line and can be split across chunks, so the tail of
 * the buffer is held back until its terminator arrives.
 */
async function readAskStream(
  body: { run_id: string; question: string },
  onEvent: (event: Record<string, unknown>) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`ask/stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch {
          // A frame we can't parse is not worth killing the stream over.
        }
      }
      split = buffer.indexOf("\n\n");
    }
  }
}

const SUGGESTIONS = [
  "What's the auto rate for this run?",
  "How many open exceptions, and what's at risk?",
  "What's the cash position?",
];

// The model can sit on a question for the better part of a minute; the panel
// says so rather than looking hung.
const PATIENCE_SECONDS = 6;

function BotMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3.5" y="8" width="17" height="12" rx="3" />
      <path d="M12 8V4.5" />
      <circle cx="12" cy="3.4" r="1.1" fill="currentColor" stroke="none" />
      <path d="M9 13v1.5M15 13v1.5" />
      <path d="M10 17.2h4" />
    </svg>
  );
}

export function Lyra() {
  const session = useSession();
  const pathname = usePathname();

  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  // Seconds spent waiting on the current question, counted by the tick below
  // rather than read off the clock during render.
  const [waited, setWaited] = useState(0);

  // Lyra is grounded on the run whose surface she is open on, and renders
  // nowhere else, so this is the only run id there is.
  const runId = pathname.match(/^\/runs\/([^/]+)/)?.[1] ?? null;

  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const username = session.status === "authenticated" ? session.user.username : null;

  // The greeting is written here, not fetched; it costs no model call.
  function openPanel() {
    setOpen(true);
    setMessages((prev) =>
      prev.length > 0
        ? prev
        : [
            {
              from: "lyra",
              text: `Hi ${username ?? "there"}, I'm Lyra. Ask me about this run: its match rate, what's sitting in exceptions, or where the cash lands. I read the same verified figures the scoreboard shows, so I won't guess a number.`,
            },
          ],
    );
  }

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, draft, busy]);

  // A stream nobody is watching is still costing tokens.
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setWaited((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function submit(q: string) {
    const text = q.trim();
    if (!text || busy || !runId) return;

    setMessages((prev) => [...prev, { from: "you", text }]);
    setQuestion("");

    setWaited(0);
    setBusy(true);
    setDraft("");

    const controller = new AbortController();
    abortRef.current = controller;
    let draftText = "";
    let settled = false;

    try {
      await readAskStream(
        { run_id: runId, question: text },
        (event) => {
          switch (event.type) {
            case "delta":
              draftText += String(event.text ?? "");
              setDraft(draftText);
              break;
            case "reset":
              // The model that wrote this failed; the backup starts clean.
              draftText = "";
              setDraft("");
              break;
            case "done": {
              settled = true;
              // Always render the answer the server settled on, never the
              // accumulated draft: an ungrounded stream is replaced wholesale.
              const answer = String(event.answer ?? "");
              setMessages((prev) => [
                ...prev,
                {
                  from: "lyra",
                  text: answer,
                  degraded: Boolean(event.degraded) || event.grounded === false,
                },
              ]);
              setDraft("");
              break;
            }
            default:
              break;
          }
        },
        controller.signal,
      );

      if (!settled) {
        setMessages((prev) => [
          ...prev,
          {
            from: "lyra",
            text: "That answer didn't finish coming through. Try asking again.",
            degraded: true,
          },
        ]);
        setDraft("");
      }
    } catch {
      if (!controller.signal.aborted) {
        setMessages((prev) => [
          ...prev,
          {
            from: "lyra",
            text: "That didn't get through. The agent may be rate-limited; try again in a moment.",
            degraded: true,
          },
        ]);
      }
      setDraft("");
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  if (session.status !== "authenticated" || !runId) return null;

  return (
    <>
      {/* The handle: bottom right, clear of the status strip. */}
      <button
        type="button"
        onClick={openPanel}
        aria-expanded={open}
        aria-controls="lyra-panel"
        className={
          "fixed bottom-[4.75rem] right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-rail text-white shadow-[0_2px_10px_rgba(16,20,26,0.28)] transition-transform hover:scale-105 md:bottom-11 md:right-6 " +
          (open ? "pointer-events-none scale-90 opacity-0" : "")
        }
      >
        <BotMark size={24} />
        <span
          aria-hidden
          className="absolute right-2.5 top-2.5 h-1.5 w-1.5 rounded-full bg-readout-hi"
        />
        <span className="sr-only">Ask Lyra</span>
      </button>

      {/* Scrim */}
      <div
        onClick={() => setOpen(false)}
        aria-hidden
        className={
          "fixed inset-0 z-40 bg-[rgb(6_9_14/0.32)] transition-opacity duration-200 " +
          (open ? "opacity-100" : "pointer-events-none opacity-0")
        }
      />

      {/* The drawer, pulled in from the right. */}
      <aside
        id="lyra-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Lyra: ask about a run"
        className={
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-[26rem] flex-col border-l border-hairline bg-surface transition-transform duration-250 ease-out " +
          (open ? "translate-x-0" : "translate-x-full")
        }
      >
        <header className="flex shrink-0 items-center gap-3 bg-rail px-4 py-3 text-white">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[3px] bg-white/10">
            <BotMark size={21} />
          </span>
          <span className="min-w-0">
            <span className="block text-[16.5px] font-semibold leading-tight tracking-[-0.01em]">
              Lyra
            </span>
            <span className="mono block truncate text-[11.5px] tracking-[0.08em] text-rail-ink">
              GROUNDED ON RUN {runId.slice(0, 8)}
            </span>
          </span>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-[3px] text-rail-ink hover:bg-white/10 hover:text-white"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              aria-hidden
            >
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
            <span className="sr-only">Close</span>
          </button>
        </header>

        <div ref={listRef} className="flex-1 space-y-3.5 overflow-y-auto p-4">
          {messages.map((m, i) =>
            m.from === "you" ? (
              <p
                key={i}
                className="ml-auto max-w-[85%] rounded-[3px] border border-hairline bg-sunk px-3 py-2 text-[14.5px] leading-relaxed"
              >
                {m.text}
              </p>
            ) : (
              <div key={i} className="max-w-[92%]">
                <span className="legend mb-1.5">Lyra</span>
                <div
                  className={
                    "border-l-2 pl-3 text-[14.5px] " +
                    (m.degraded ? "border-hairline-strong text-muted" : "border-readout-hi")
                  }
                >
                  <Markdown text={m.text} />
                </div>
              </div>
            ),
          )}

          {draft && (
            <div className="max-w-[92%]">
              <span className="legend mb-1.5">Lyra</span>
              <div className="border-l-2 border-readout-hi pl-3 text-[14.5px]">
                <Markdown text={draft} />
                <span
                  aria-hidden
                  className="pulse-dot ml-0.5 inline-block h-3.5 w-[2px] translate-y-[2px] bg-readout-hi"
                />
              </div>
            </div>
          )}

          {busy && !draft && (
            <div className="flex items-center gap-2.5 text-[13.5px] text-muted">
              <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
              {waited > PATIENCE_SECONDS
                ? `Still working; the agent can take up to a minute (${waited}s)`
                : "Thinking…"}
            </div>
          )}

          {messages.length <= 1 && !busy && !draft && (
            <div className="flex flex-wrap gap-2 pt-1">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => submit(s)}
                  className="btn btn-sm text-left"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
          className="flex shrink-0 items-center gap-2 border-t border-hairline p-3"
        >
          <label className="sr-only" htmlFor="lyra-input">
            Ask Lyra a question
          </label>
          <input
            ref={inputRef}
            id="lyra-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about this run…"
            className="field flex-1"
          />
          <button type="submit" disabled={busy || !question.trim()} className="btn btn-primary">
            Send
          </button>
        </form>
      </aside>
    </>
  );
}
