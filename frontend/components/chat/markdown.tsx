"use client";

import { Fragment, memo, useMemo, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The assistant's answers as formatted text: headings, lists, tables, bold,
 * italics, code, links — and `[n]` source markers as chips that open the
 * source.
 *
 * A small renderer of our own rather than a Markdown library: the answer is
 * model output, so nothing in it is ever treated as HTML (every piece
 * becomes a React text node), links are only followed when they are http(s),
 * and the grammar is just what the prompts ask the model to write.
 */

interface MarkdownProps {
  text: string;
  /** Markers that resolve to a real citation; others render as plain chips. */
  known?: ReadonlySet<number>;
  activeMarker?: number | null;
  onCite?: (marker: number) => void;
  streaming?: boolean;
  className?: string;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "quote"; text: string }
  | { kind: "rule" };

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,3}[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 3, text: heading[2] ?? "" });
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
      continue;
    }
    if (TABLE_ROW.test(line) && TABLE_RULE.test(lines[i + 1] ?? "")) {
      flush();
      const header = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && TABLE_ROW.test(lines[i] ?? "")) {
        rows.push(cells(lines[i] ?? ""));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "table", header, rows });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    if (bullet || numbered) {
      flush();
      const ordered = !bullet;
      const items: string[] = [];
      while (i < lines.length) {
        const current = lines[i] ?? "";
        const match = (ordered ? NUMBERED : BULLET).exec(current);
        if (match) {
          items.push(match[1] ?? "");
        } else if (current.trim() && /^\s{2,}\S/.test(current) && items.length) {
          items[items.length - 1] += ` ${current.trim()}`; // a wrapped item
        } else {
          break;
        }
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flush();
      blocks.push({ kind: "quote", text: line.replace(/^\s*>\s?/, "") });
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

const INLINE =
  /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|`[^`]+`|\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;

function markerList(inner: string): number[] {
  const out: number[] = [];
  for (const part of inner.split(/\s*,\s*/)) {
    const range = part.split(/\s*[–-]\s*/);
    const [a, b] = range.map(Number);
    if (range.length === 2 && a !== undefined && b !== undefined && b > a && b - a < 20) {
      for (let n = a; n <= b; n += 1) out.push(n);
    } else if (a !== undefined && !Number.isNaN(a)) {
      out.push(a);
    }
  }
  return out;
}

interface InlineContext {
  known?: ReadonlySet<number>;
  activeMarker?: number | null;
  onCite?: (marker: number) => void;
  streaming: boolean;
}

function inline(text: string, ctx: InlineContext, keyPrefix = ""): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    const token = match[0];
    if (at > last) out.push(<Fragment key={`${keyPrefix}t${index}`}>{text.slice(last, at)}</Fragment>);
    const key = `${keyPrefix}i${index}`;
    if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={key}>{inline(token.slice(2, -2), ctx, `${key}-`)}</strong>);
    } else if (token.startsWith("`")) {
      out.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("*")) {
      out.push(<em key={key}>{inline(token.slice(1, -1), ctx, `${key}-`)}</em>);
    } else if (/^\[\d/.test(token)) {
      for (const marker of markerList(token.slice(1, -1))) {
        const resolvable = !ctx.streaming && ctx.onCite && (ctx.known?.has(marker) ?? true);
        out.push(
          resolvable ? (
            <button
              key={`${key}-${marker}`}
              type="button"
              className="cite-chip mx-0.5"
              data-active={ctx.activeMarker === marker}
              onClick={() => ctx.onCite?.(marker)}
              aria-label={`Open source ${marker}`}
              title={`Source ${marker}`}
            >
              {marker}
            </button>
          ) : (
            <span key={`${key}-${marker}`} className="cite-chip mx-0.5 opacity-70" aria-hidden>
              {marker}
            </span>
          ),
        );
      }
    } else {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
      if (link) {
        out.push(
          <a
            key={key}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-primary underline underline-offset-2"
          >
            {link[1]}
          </a>,
        );
      } else {
        out.push(<Fragment key={key}>{token}</Fragment>);
      }
    }
    last = at + token.length;
    index += 1;
  }
  if (last < text.length) out.push(<Fragment key={`${keyPrefix}tail`}>{text.slice(last)}</Fragment>);
  return out;
}

export const Markdown = memo(function Markdown({
  text,
  known,
  activeMarker,
  onCite,
  streaming = false,
  className,
}: MarkdownProps) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  const ctx: InlineContext = { known, activeMarker, onCite, streaming };
  const caret = streaming ? (
    <span aria-hidden className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-foreground align-middle" />
  ) : null;

  return (
    <div className={cn("prose-answer space-y-3", className)} aria-live={streaming ? "polite" : undefined}>
      {blocks.map((block, bi) => {
        const isLast = bi === blocks.length - 1;
        switch (block.kind) {
          case "heading": {
            const size = block.level <= 2 ? "text-base" : "text-[15px]";
            return (
              <p key={bi} role="heading" aria-level={Math.min(block.level + 1, 6)} className={cn("pt-1 font-semibold tracking-tight", size)}>
                {inline(block.text, ctx, `b${bi}`)}
              </p>
            );
          }
          case "list": {
            const List = block.ordered ? "ol" : "ul";
            return (
              <List key={bi} className={cn("space-y-1.5 pl-5", block.ordered ? "list-decimal" : "list-disc")}>
                {block.items.map((item, ii) => (
                  <li key={ii} className="pl-1 marker:text-muted-foreground">
                    {inline(item, ctx, `b${bi}l${ii}`)}
                    {isLast && ii === block.items.length - 1 && caret}
                  </li>
                ))}
              </List>
            );
          }
          case "table":
            return (
              <div key={bi} className="overflow-x-auto rounded-md border">
                <table className="w-full border-collapse text-sm">
                  <thead className="bg-muted/60">
                    <tr>
                      {block.header.map((h, hi) => (
                        <th key={hi} className="border-b px-3 py-2 text-left font-medium">
                          {inline(h, ctx, `b${bi}h${hi}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, ri) => (
                      <tr key={ri} className="even:bg-muted/20">
                        {row.map((cell, ci) => (
                          <td key={ci} className="border-b px-3 py-2 align-top">
                            {inline(cell, ctx, `b${bi}r${ri}c${ci}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "quote":
            return (
              <blockquote key={bi} className="border-l-2 pl-3 text-muted-foreground">
                {inline(block.text, ctx, `b${bi}`)}
              </blockquote>
            );
          case "rule":
            return <hr key={bi} className="border-border" />;
          default:
            return (
              <p key={bi}>
                {inline(block.text, ctx, `b${bi}`)}
                {isLast && caret}
              </p>
            );
        }
      })}
      {blocks.length === 0 && streaming && <p>{caret}</p>}
    </div>
  );
});
