/**
 * Voice for the chat: hear a spoken question, read the answer aloud.
 *
 * Three small pieces around the ordinary chat, so a spoken turn is exactly a
 * typed turn — same guardrails, same sources, same saved conversation:
 *
 *   - `listenForUtterance` records one spoken question and stops by itself
 *     when the person stops talking (an energy-based voice detector that
 *     learns the room's noise floor first);
 *   - `transcribe` sends the clip to POST /api/v1/voice/transcribe
 *     (Deepgram's medical model on the server);
 *   - `SpeechQueue` reads the answer aloud a few sentences at a time as it
 *     streams, through POST /api/v1/voice/speak, and falls back to the
 *     browser's own voice when the server cannot speak.
 */

import { apiUrl } from "@/lib/api";

// -- listening --------------------------------------------------------------------------

export interface ListenOptions {
  signal: AbortSignal;
  onLevel?: (level: number) => void;
  onSpeechStart?: () => void;
  /** Give up when nobody speaks for this long. */
  noSpeechMs?: number;
  /** The pause that ends a question. */
  endSilenceMs?: number;
  maxMs?: number;
}

function pickMimeType(): string {
  for (const type of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/**
 * Record one utterance from `stream`. Resolves with the clip, or null when
 * nobody spoke (or the signal aborted first).
 */
export function listenForUtterance(stream: MediaStream, options: ListenOptions): Promise<Blob | null> {
  const { signal, onLevel, onSpeechStart } = options;
  const noSpeechMs = options.noSpeechMs ?? 9000;
  const endSilenceMs = options.endSilenceMs ?? 1200;
  const maxMs = options.maxMs ?? 30000;

  return new Promise((resolve) => {
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: BlobPart[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };

    const started = performance.now();
    let floor = 0;
    let calibratedMs = 0;
    let voicedMs = 0;
    let speaking = false;
    let lastVoice = 0;
    let done = false;
    const step = 50;

    const finish = (keep: boolean) => {
      if (done) return;
      done = true;
      clearInterval(timer);
      signal.removeEventListener("abort", onAbort);
      onLevel?.(0);
      const deliver = () => {
        void context.close();
        const type = (recorder.mimeType || "audio/webm").split(";")[0] ?? "audio/webm";
        resolve(keep && chunks.length ? new Blob(chunks, { type }) : null);
      };
      if (recorder.state === "inactive") deliver();
      else {
        recorder.onstop = deliver;
        recorder.stop();
      }
    };
    const onAbort = () => finish(false);
    signal.addEventListener("abort", onAbort);

    recorder.start(250);
    const timer = setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const s of samples) sum += s * s;
      const rms = Math.sqrt(sum / samples.length);
      onLevel?.(Math.min(1, rms * 6));
      const now = performance.now();
      // The first 400 ms learn the room; speech has to stand well above it.
      if (calibratedMs < 400) {
        floor = calibratedMs === 0 ? rms : floor * 0.8 + rms * 0.2;
        calibratedMs += step;
        return;
      }
      const threshold = Math.max(0.015, floor * 3);
      if (rms > threshold) {
        voicedMs += step;
        lastVoice = now;
        if (!speaking && voicedMs >= 150) {
          speaking = true;
          onSpeechStart?.();
        }
      } else if (!speaking) {
        voicedMs = Math.max(0, voicedMs - step);
        floor = floor * 0.95 + rms * 0.05; // follow a drifting background
      }
      if (speaking && now - lastVoice > endSilenceMs) finish(true);
      else if (!speaking && now - started > noSpeechMs) finish(false);
      else if (now - started > maxMs) finish(speaking);
    }, step);
  });
}

export async function transcribe(clip: Blob, token: string): Promise<string> {
  const response = await fetch(apiUrl("/api/v1/voice/transcribe"), {
    method: "POST",
    headers: { "Content-Type": clip.type || "audio/webm", Authorization: `Bearer ${token}` },
    body: clip,
  });
  const body = (await response.json().catch(() => ({}))) as {
    text?: string;
    error?: { message?: string };
  };
  if (!response.ok) throw new Error(body.error?.message ?? "Couldn't transcribe that.");
  return (body.text ?? "").trim();
}

// -- speaking ---------------------------------------------------------------------------

/** Answer text as it should be heard: no markdown, markers, links or tables. */
export function speakable(text: string): string {
  return text
    .split("\n")
    .filter((line) => !/^\s*sources?\s*:/i.test(line) && !/^\s*\|/.test(line))
    .map((line) => line.replace(/^\s*(?:[-*•]|\d{1,3}[.)])\s+/, "").replace(/^\s*#{1,6}\s+/, ""))
    .join("\n")
    .replace(/\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]/g, "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_`>]+/g, "")
    .replace(/([^.!?:\s])\s*\n+\s*/g, "$1. ")
    .replace(/\s*\n+\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

const CHUNK_CHARS = 320;
const SENTENCE_END = /[.!?]+(?=\s|$)/g;

/**
 * Splits streaming answer text into speakable chunks at sentence ends. The
 * first sentence goes as soon as it is complete, so speech starts quickly;
 * later ones are grouped to about `CHUNK_CHARS` so there are few requests.
 */
export class SentenceChunker {
  private spokenUpTo = 0;
  private first = true;

  /** New chunks ready to speak, given the whole answer so far. */
  take(text: string, final = false): string[] {
    const clean = speakable(text);
    const out: string[] = [];
    for (;;) {
      const rest = clean.slice(this.spokenUpTo);
      const ends: number[] = [];
      SENTENCE_END.lastIndex = 0;
      for (let m = SENTENCE_END.exec(rest); m; m = SENTENCE_END.exec(rest)) {
        const end = m.index + m[0].length;
        // A full stop at the very end of streaming text may be mid-number.
        if (!final && end >= rest.length) break;
        ends.push(end);
      }
      const cut = this.first ? ends[0] : ends.find((e) => e >= CHUNK_CHARS);
      if (cut === undefined) break;
      const chunk = rest.slice(0, cut).trim();
      if (chunk) out.push(chunk);
      this.spokenUpTo += cut;
      this.first = false;
    }
    if (final) {
      const tail = clean.slice(this.spokenUpTo).trim();
      if (tail) out.push(tail);
      this.spokenUpTo = clean.length;
    }
    return out;
  }
}

type Clip = { kind: "audio"; blob: Blob } | { kind: "browser"; text: string };

/** How answers are read aloud: the Settings choice of voice and pace. */
export interface SpeechOptions {
  /** A voice name the server offers (/voice/voices); absent, the server's own. */
  voice?: string;
  /** Playback pace, 0.75–1.5; pitch is kept. */
  rate?: number;
}

/**
 * Plays chunks in order, fetching the next while the current one plays.
 * `onIdle` fires when everything queued has been heard.
 */
export class SpeechQueue {
  private queue: Array<Promise<Clip>> = [];
  private audio: HTMLAudioElement | null = null;
  private playing = false;
  private stopped = false;
  private serverFailed = false;
  private pulse: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly token: string,
    private readonly handlers: {
      onStart?: () => void;
      onIdle?: () => void;
      onLevel?: (level: number) => void;
    },
    private readonly speech: SpeechOptions = {},
  ) {
    // Signed out (the website's chatbot) the server will not speak: use the
    // browser's voice from the start instead of failing each chunk.
    this.serverFailed = !token;
  }

  get busy(): boolean {
    return this.playing || this.queue.length > 0;
  }

  private async fetchClip(text: string): Promise<Clip> {
    if (!this.serverFailed) {
      try {
        const response = await fetch(apiUrl("/api/v1/voice/speak"), {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.token}` },
          body: JSON.stringify({
            text: text.slice(0, 1200),
            ...(this.speech.voice ? { voice: this.speech.voice } : {}),
          }),
        });
        if (response.ok) return { kind: "audio", blob: await response.blob() };
        // The server cannot speak at all: stop asking for this conversation.
        if (response.status === 503) this.serverFailed = true;
      } catch {
        /* network: fall through to the browser's voice for this chunk */
      }
    }
    return { kind: "browser", text };
  }

  enqueue(text: string): void {
    if (!text.trim()) return;
    this.stopped = false;
    this.queue.push(this.fetchClip(text));
    if (!this.playing) void this.drain();
  }

  private async drain(): Promise<void> {
    this.playing = true;
    this.handlers.onStart?.();
    this.pulse = setInterval(() => this.handlers.onLevel?.(0.25 + Math.random() * 0.55), 140);
    while (this.queue.length && !this.stopped) {
      const clip = await this.queue.shift()!;
      if (this.stopped) break;
      if (clip.kind === "audio") await this.playAudio(clip.blob);
      else await this.playBrowser(clip.text);
    }
    if (this.pulse) clearInterval(this.pulse);
    this.pulse = null;
    this.playing = false;
    this.handlers.onLevel?.(0);
    if (!this.stopped) this.handlers.onIdle?.();
  }

  private playAudio(blob: Blob): Promise<void> {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.playbackRate = this.speech.rate ?? 1;
      this.audio = audio;
      const end = () => {
        URL.revokeObjectURL(url);
        if (this.audio === audio) this.audio = null;
        resolve();
      };
      audio.onended = end;
      audio.onerror = end;
      audio.onpause = end;
      audio.play().catch(end);
    });
  }

  private playBrowser(text: string): Promise<void> {
    return new Promise((resolve) => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) return resolve();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "en-IN";
      utterance.rate = this.speech.rate ?? 1;
      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();
      window.speechSynthesis.speak(utterance);
    });
  }

  /** Stop now: drop what is queued and cut what is playing. */
  stop(): void {
    this.stopped = true;
    this.queue = [];
    this.audio?.pause();
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
  }
}
