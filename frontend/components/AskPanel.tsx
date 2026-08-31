"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";

const SUGGESTED_QUESTIONS = [
  "What's the auto rate for this run?",
  "How many open exceptions are there, and what's at risk?",
  "What's the cash forecast for this run?",
];

type Exchange = { question: string; answer: string; degraded: boolean };

export function AskPanel({ runId }: { runId: string }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);

  async function submit(q: string) {
    if (!q.trim() || busy) return;
    setBusy(true);
    setQuestion("");

    const { data } = await api.POST("/ask", { body: { run_id: runId, question: q } });

    setBusy(false);
    setExchanges((prev) => [
      ...prev,
      { question: q, answer: data?.answer ?? "I do not have that.", degraded: data?.degraded ?? true },
    ]);
  }

  return (
    <details className="mt-2 border border-hairline">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Ask about this run</summary>
      <div className="border-t border-hairline p-3">
        {exchanges.length === 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {SUGGESTED_QUESTIONS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => submit(q)}
                className="border border-hairline px-2 py-1 text-xs text-muted hover:border-foreground hover:text-foreground"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        <ul className="flex flex-col gap-3 text-sm">
          {exchanges.map((exchange, index) => (
            <li key={index}>
              <p className="font-medium">{exchange.question}</p>
              <p className={exchange.degraded ? "mt-1 text-muted" : "mt-1"}>{exchange.answer}</p>
            </li>
          ))}
        </ul>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
          className="mt-3 flex gap-2"
        >
          <label className="sr-only" htmlFor="ask-question">
            Ask a question about this run
          </label>
          <input
            id="ask-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask a question about this run…"
            className="flex-1 border border-hairline px-2 py-1.5 text-sm"
          />
          <button
            type="submit"
            disabled={busy}
            className="border border-foreground bg-foreground px-3 py-1.5 text-sm font-medium text-background disabled:opacity-50"
          >
            {busy ? "Asking…" : "Ask"}
          </button>
        </form>
      </div>
    </details>
  );
}
