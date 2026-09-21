import { inflateSync } from "node:zlib";

/**
 * Enough PDF reading to check that an exported file really carries the text
 * it is supposed to — the citations, the question, the answer.
 *
 * A PDF's page content lives in stream objects, usually Flate-compressed;
 * inside, text is drawn with `(literal) Tj` or `[<0043> -62 <004c>] TJ`.
 * @react-pdf/renderer writes the hex form, one glyph per code, and its
 * embedded subsets keep the ASCII code points — so decoding hex as Latin-1
 * reads back the words. This is not a general PDF parser; it is a real read
 * of the bytes on disk instead of trusting the generator, and when the
 * encoding does not line up the assertion fails loudly rather than passing
 * quietly.
 */
export function pdfText(bytes: Buffer): string {
  const out: string[] = [];
  let index = 0;
  while (true) {
    const start = bytes.indexOf("stream", index);
    if (start < 0) break;
    const end = bytes.indexOf("endstream", start);
    if (end < 0) break;
    let from = start + "stream".length;
    if (bytes[from] === 0x0d) from += 1;
    if (bytes[from] === 0x0a) from += 1;
    const raw = bytes.subarray(from, end);
    let content: Buffer;
    try {
      content = inflateSync(raw);
    } catch {
      content = raw; // uncompressed stream, or one we cannot read
    }
    const text = content.toString("latin1");
    out.push(extractLiterals(text));
    out.push(extractHexStrings(text));
    index = end + "endstream".length;
  }
  return out.join("\n");
}

function extractLiterals(stream: string): string {
  const pieces: string[] = [];
  let depth = 0;
  let current = "";
  for (let i = 0; i < stream.length; i += 1) {
    const ch = stream[i];
    if (ch === "\\" && depth > 0) {
      const next = stream[i + 1];
      // PDF escapes: \( \) \\ and octal codes.
      if (next === "(" || next === ")" || next === "\\") {
        current += next;
        i += 1;
        continue;
      }
      if (next >= "0" && next <= "7") {
        const octal = stream.slice(i + 1, i + 4);
        current += String.fromCharCode(parseInt(octal, 8));
        i += octal.length;
        continue;
      }
      i += 1;
      continue;
    }
    if (ch === "(") {
      depth += 1;
      if (depth === 1) current = "";
      else current += ch;
      continue;
    }
    if (ch === ")") {
      depth -= 1;
      if (depth === 0) pieces.push(current);
      else current += ch;
      continue;
    }
    if (depth > 0) current += ch;
  }
  return pieces.join("");
}

/** `<48656c6c6f>` → "Hello", for the hex string form used in TJ arrays. */
function extractHexStrings(stream: string): string {
  const pieces: string[] = [];
  const pattern = /<([0-9A-Fa-f\s]+)>/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(stream)) !== null) {
    const hex = match[1].replace(/\s+/g, "");
    if (hex.length === 0 || hex.length % 2 !== 0) continue;
    let word = "";
    for (let i = 0; i < hex.length; i += 2) {
      const code = parseInt(hex.slice(i, i + 2), 16);
      word += code >= 32 && code < 127 ? String.fromCharCode(code) : "";
    }
    pieces.push(word);
  }
  return pieces.join("");
}

export function isPdf(bytes: Buffer): boolean {
  return bytes.subarray(0, 5).toString("latin1") === "%PDF-";
}
