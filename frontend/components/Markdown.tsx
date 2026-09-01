import type { ReactNode } from "react";

/**
 * A deliberately small Markdown subset, for one job: rendering what the ask
 * agent writes.
 *
 * It builds React elements rather than an HTML string, so model output is
 * escaped by React and there is no `dangerouslySetInnerHTML` anywhere near
 * it: the answer text is untrusted, and a full Markdown library would bring
 * raw-HTML passthrough along with it.
 *
 * Supported: paragraphs, `-`/`*`/`1.` lists, headings, fenced and inline
 * code, **bold**, *italic*, and ~~strikethrough~~. Anything else renders as
 * the literal characters the model typed, which is the right failure mode
 * for a half-written token arriving mid-stream.
 */

type Inline = { text: string; bold?: boolean; italic?: boolean; code?: boolean; strike?: boolean };

// Ordered so the two-character markers are tried before their one-character
// prefixes; otherwise `**bold**` would match the italic rule first.
const INLINE_RULES: { re: RegExp; mark: keyof Omit<Inline, "text"> }[] = [
  { re: /\*\*([\s\S]+?)\*\*/, mark: "bold" },
  { re: /__([\s\S]+?)__/, mark: "bold" },
  { re: /~~([\s\S]+?)~~/, mark: "strike" },
  { re: /(?<!\*)\*(?!\s)([^*\n]+?)\*(?!\*)/, mark: "italic" },
  { re: /(?<!_)_(?!\s)([^_\n]+?)_(?!_)/, mark: "italic" },
];

function parseInline(text: string): Inline[] {
  // Code spans win outright: nothing inside backticks is markup.
  const codeSplit = text.split(/(`[^`\n]+`)/);
  const out: Inline[] = [];

  for (const chunk of codeSplit) {
    if (!chunk) continue;
    if (chunk.startsWith("`") && chunk.endsWith("`") && chunk.length > 1) {
      out.push({ text: chunk.slice(1, -1), code: true });
      continue;
    }
    out.push(...parseEmphasis(chunk));
  }
  return out;
}

function parseEmphasis(text: string, inherited: Omit<Inline, "text"> = {}): Inline[] {
  for (const { re, mark } of INLINE_RULES) {
    const match = re.exec(text);
    if (!match || match.index === undefined) continue;
    const before = text.slice(0, match.index);
    const after = text.slice(match.index + match[0].length);
    return [
      ...(before ? parseEmphasis(before, inherited) : []),
      ...parseEmphasis(match[1], { ...inherited, [mark]: true }),
      ...(after ? parseEmphasis(after, inherited) : []),
    ];
  }
  return text ? [{ text, ...inherited }] : [];
}

function Spans({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((span, i) => {
        if (span.code) {
          return (
            <code
              key={i}
              className="mono rounded-[2px] border border-hairline bg-sunk px-1 py-px text-[0.92em]"
            >
              {span.text}
            </code>
          );
        }
        let node: ReactNode = span.text;
        if (span.bold) node = <strong className="font-semibold">{node}</strong>;
        if (span.italic) node = <em>{node}</em>;
        if (span.strike) node = <s className="text-faint">{node}</s>;
        return <span key={i}>{node}</span>;
      })}
    </>
  );
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "pre"; text: string };

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      blocks.push({ kind: "p", text: paragraph.join(" ") });
      paragraph = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.trimStart().startsWith("```")) {
      flush();
      const body: string[] = [];
      i++;
      // An unterminated fence is normal mid-stream: take the rest as code.
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) body.push(lines[i++]);
      blocks.push({ kind: "pre", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "h", level: heading[1].length, text: heading[2] });
      continue;
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      flush();
      const last = blocks[blocks.length - 1];
      if (last && last.kind === "ul") last.items.push(bullet[1]);
      else blocks.push({ kind: "ul", items: [bullet[1]] });
      continue;
    }

    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      flush();
      const last = blocks[blocks.length - 1];
      if (last && last.kind === "ol") last.items.push(numbered[1]);
      else blocks.push({ kind: "ol", items: [numbered[1]] });
      continue;
    }

    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const blocks = parseBlocks(text);

  return (
    <div className={"flex flex-col gap-2.5 " + className}>
      {blocks.map((block, i) => {
        switch (block.kind) {
          case "h":
            return (
              <p key={i} className="text-[13.5px] font-semibold tracking-[-0.01em]">
                <Spans text={block.text} />
              </p>
            );
          case "ul":
            return (
              <ul key={i} className="flex flex-col gap-1.5 pl-1">
                {block.items.map((item, j) => (
                  <li key={j} className="flex gap-2">
                    <span
                      aria-hidden
                      className="mt-[0.55em] h-1 w-1 shrink-0 rounded-full bg-faint"
                    />
                    <span className="min-w-0">
                      <Spans text={item} />
                    </span>
                  </li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i} className="flex flex-col gap-1.5 pl-1">
                {block.items.map((item, j) => (
                  <li key={j} className="flex gap-2">
                    <span className="mono shrink-0 text-faint">{j + 1}.</span>
                    <span className="min-w-0">
                      <Spans text={item} />
                    </span>
                  </li>
                ))}
              </ol>
            );
          case "pre":
            return (
              <pre
                key={i}
                className="mono overflow-x-auto rounded-[3px] border border-hairline bg-sunk p-2.5 text-[11.5px] leading-relaxed"
              >
                {block.text}
              </pre>
            );
          default:
            return (
              <p key={i} className="leading-relaxed">
                <Spans text={block.text} />
              </p>
            );
        }
      })}
    </div>
  );
}
