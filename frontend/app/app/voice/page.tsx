"use client";

import { Keyboard, MessagesSquare, Mic, MicOff, Square } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AgentTranscript } from "@/components/voice/agent-transcript";
import { MicCheck, readSavedMicId } from "@/components/voice/mic-check";
import { ConfirmCard } from "@/components/voice/confirm-card";
import { StateIndicator } from "@/components/voice/state-indicator";
import { UserTranscript } from "@/components/voice/transcript";
import { WaterfallChart, type WaterfallRow } from "@/components/voice/waterfall-chart";
import { Waveform } from "@/components/voice/waveform";
import { useVoiceConfig } from "@/hooks/use-api";
import { useVoiceSession, type VoiceTurn } from "@/hooks/use-voice-session";
import { warmApi } from "@/lib/signup";

/** Reads `?session=` (handoff from the text Ask screen) outside the page's render. */
function SessionFromUrl({ onSession }: { onSession: (id: string | null) => void }) {
  const params = useSearchParams();
  const session = params.get("session");
  useEffect(() => onSession(session), [session, onSession]);
  return null;
}

export default function VoicePage() {
  const [querySessionId, setQuerySessionId] = useState<string | null>(null);
  return (
    <>
      <Suspense fallback={null}>
        <SessionFromUrl onSession={setQuerySessionId} />
      </Suspense>
      <VoiceScreen querySessionId={querySessionId} />
    </>
  );
}

function VoiceScreen({ querySessionId }: { querySessionId: string | null }) {
  const voice = useVoiceSession({ querySessionId });
  const config = useVoiceConfig();
  const { state } = voice;
  const [typed, setTyped] = useState("");
  const [micId, setMicId] = useState<string | undefined>(undefined);
  const active = state.status === "ready" || state.status === "reconnecting";
  // The server says up front whether it can run voice (no engine, no key);
  // `undefined` while the config is loading counts as available.
  const unavailable = config.data?.available === false ? (config.data.unavailable_reason ?? "") : null;

  // A sleeping free-tier API takes up to two minutes to wake, and a voice
  // connection opened against it fails; waking it on arrival hides that.
  useEffect(warmApi, []);
  useEffect(() => setMicId(readSavedMicId()), []);
  const turns = useMemo(() => state.turns.filter((t) => t.final || t.partial || t.sentences.length), [state.turns]);
  const current = state.turns[state.turnIndex] ?? null;
  const pendingConfirm = turns.find((t) => t.confirm) ?? null;
  const threadId = state.querySessionId ?? querySessionId;

  const rows: WaterfallRow[] = useMemo(
    () =>
      state.turns
        .filter((t) => t.waterfall)
        .map((t) => ({
          label: `turn ${t.index + 1}`,
          legs: t.waterfall?.legs ?? {},
          total: t.waterfall?.clientFirstAudioMs ?? t.waterfall?.totalFirstAudioMs ?? null,
        })),
    [state.turns],
  );

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Voice</h1>
          <p className="text-sm text-muted-foreground">
            Hands-free: ask, listen, interrupt. Every guardrail runs exactly as it does for text.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {threadId && (
            <Button
              variant="ghost"
              size="sm"
              nativeButton={false}
              render={<Link href={`/app?session=${threadId}`} />}
            >
              <Keyboard /> Continue in text
            </Button>
          )}
          {threadId && (
            <Button
              variant="ghost"
              size="sm"
              nativeButton={false}
              render={<Link href={`/app/sessions/${threadId}`} />}
            >
              <MessagesSquare /> This thread
            </Button>
          )}
        </div>
      </header>

      <section className="rounded-lg border bg-card p-4" aria-label="Voice controls">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <StateIndicator state={active ? state.state : "IDLE"} />
          <div className="flex items-center gap-2">
            {!active ? (
              <Button
                size="lg"
                onClick={() => void voice.start(micId)}
                disabled={state.status === "connecting" || unavailable !== null}
                data-testid="voice-start"
              >
                <Mic /> {state.status === "connecting" ? "Starting…" : "Start listening"}
              </Button>
            ) : (
              <>
                {state.state === "SPEAKING" && (
                  <Button variant="outline" size="lg" onClick={voice.interrupt} data-testid="voice-interrupt">
                    <Square /> Interrupt
                  </Button>
                )}
                <Button variant="outline" size="lg" onClick={() => void voice.stop()} data-testid="voice-stop">
                  <MicOff /> Stop
                </Button>
              </>
            )}
          </div>
        </div>
        {unavailable !== null && (
          <Alert className="mt-3" data-testid="voice-unavailable">
            <AlertTitle>Voice isn&apos;t available on this server yet</AlertTitle>
            <AlertDescription>
              {unavailable || "The server has no speech engine configured."} You can still ask the same
              questions by typing on the Ask page.
            </AlertDescription>
          </Alert>
        )}
        {!active && unavailable === null && (
          <MicCheck deviceId={micId} onDeviceChange={setMicId} className="mt-3" />
        )}
        <Waveform micLevel={state.micLevel} agentLevel={state.agentLevel} active={active} className="mt-3" />
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {config.data
            ? `${config.data.backend} · STT ${config.data.stt_model} · TTS ${config.data.tts_model} · ${config.data.boost_terms} boost terms · ${config.data.lasa_pairs} LASA pairs`
            : "…"}
          {active && state.micAvailable && (state.echoCancellation ? " · echo cancellation on" : " · echo cancellation unavailable")}
          {state.resumed && " · resumed"}
        </p>
        {active && !state.micAvailable && (
          <p className="mt-2 rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground" role="status">
            Microphone unavailable ({state.micError ?? "no device"}). Listen-only mode: type a turn below and the
            answer is spoken.
          </p>
        )}
      </section>

      {state.error && (
        <Alert variant="destructive">
          <AlertTitle>Voice session problem</AlertTitle>
          <AlertDescription>{state.error}</AlertDescription>
        </Alert>
      )}

      {pendingConfirm && <ConfirmCard turn={pendingConfirm} onChoose={voice.confirm} />}

      <section className="space-y-6" aria-live="polite" aria-label="Conversation">
        {turns.length === 0 && (
          <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
            {active
              ? "Listening. Try: “What is first-line anticoagulation in non-valvular atrial fibrillation?”"
              : "Start listening, then ask a clinical question out loud. You can interrupt the answer at any time, and say “yes” when the agent offers more."}
          </div>
        )}
        {turns.map((turn: VoiceTurn) => (
          <div key={turn.index} className="space-y-3">
            <UserTranscript turn={turn} />
            <AgentTranscript turn={turn} onContinue={voice.continueAnswer} />
            {turn.bargeIns.length > 0 && (
              <p className="ml-10 font-mono text-[11px] text-muted-foreground">
                interrupted · playback stopped in {turn.bargeIns.map((ms) => `${ms} ms`).join(", ")} (client-side)
              </p>
            )}
          </div>
        ))}
        {active && current && !current.final && !current.partial && state.state === "LISTENING" && turns.length > 0 && (
          <p className="text-sm text-muted-foreground">Listening…</p>
        )}
      </section>

      {active && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const text = typed.trim();
            if (!text) return;
            voice.sendText(text);
            setTyped("");
          }}
        >
          <Input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="Or type a turn here — same thread, same guardrails"
            aria-label="Type a question"
          />
          <Button type="submit" variant="outline">
            Send
          </Button>
        </form>
      )}

      {rows.length > 0 && (
        <section className="rounded-lg border bg-card" aria-labelledby="waterfall-heading">
          <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
            <h2 id="waterfall-heading" className="text-sm font-medium">
              Latency waterfall
              <span className="ml-2 font-mono text-xs text-muted-foreground">
                from your last word to the first audio · targets p50 ≤ 1.2 s, p95 ≤ 2.0 s
              </span>
            </h2>
          </header>
          <div className="px-2 pt-2">
            <WaterfallChart rows={rows} target={2000} />
          </div>
          <ul className="grid gap-1 px-4 pb-3 text-xs text-muted-foreground sm:grid-cols-2">
            {state.turns
              .filter((t) => t.waterfall)
              .map((t) => (
                <li key={t.index} className="font-mono">
                  turn {t.index + 1}: first audio{" "}
                  {t.waterfall?.clientFirstAudioMs !== null && t.waterfall?.clientFirstAudioMs !== undefined
                    ? `${Math.round(t.waterfall.clientFirstAudioMs)} ms (played)`
                    : t.waterfall?.totalFirstAudioMs !== null && t.waterfall?.totalFirstAudioMs !== undefined
                      ? `${Math.round(t.waterfall.totalFirstAudioMs)} ms (sent)`
                      : "n/a"}
                  {t.waterfall?.speculation.fired === true &&
                    ` · speculation ${t.waterfall.speculation.hit ? "hit" : "miss"}`}
                  {t.waterfall?.maskUsed && " · mask used"}
                </li>
              ))}
          </ul>
        </section>
      )}
    </div>
  );
}
