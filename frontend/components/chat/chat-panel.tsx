"use client";

import { Info } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { LogoMark } from "@/components/brand/logo";
import { AudienceSwitch, Composer } from "@/components/chat/composer";
import { DictationButton } from "@/components/chat/dictation";
import { AssistantBubble, UserBubble } from "@/components/chat/message";
import { VoiceBar, VoiceModeButton } from "@/components/chat/voice-bar";
import type { ChatController } from "@/hooks/use-chat";
import { useVoiceMode } from "@/hooks/use-voice-mode";
import type { Audience } from "@/lib/chat";
import { cn } from "@/lib/utils";

export const SUGGESTIONS: Record<Audience, string[]> = {
  patient: [
    "My child has a fever of 38.5°C — what should I do?",
    "What are the warning signs of dengue?",
    "Is it safe to take ibuprofen with high blood pressure?",
    "How can I lower my blood sugar naturally?",
  ],
  clinician: [
    "First-line anticoagulation in non-valvular atrial fibrillation, and dose adjustment in CKD",
    "Empirical antibiotics for community-acquired pneumonia in adults",
    "Latest evidence on SGLT2 inhibitors in heart failure with preserved EF",
    "Management of scrub typhus in pregnancy",
  ],
  student: [
    "Explain the pathophysiology of nephrotic syndrome",
    "High-yield points on the brachial plexus for MBBS exams",
    "Compare type 1 and type 2 respiratory failure",
    "What changed in the latest hypertension guidelines?",
  ],
};

/**
 * A conversation: the messages, kept scrolled to the newest while an answer
 * streams (unless the reader has scrolled up to read), and the composer.
 * Used full-page (Chat, Learn), in the floating assistant, and on the website.
 */
export function ChatPanel({
  chat,
  empty,
  compact = false,
  showAudience = true,
  placeholder,
  footer,
  checkHref,
  voice = false,
  startVoice = false,
  className,
}: {
  chat: ChatController;
  empty?: ReactNode;
  compact?: boolean;
  showAudience?: boolean;
  placeholder?: string;
  footer?: ReactNode;
  checkHref?: string;
  /** Voice typing and the spoken conversation (signed-in surfaces only). */
  voice?: boolean;
  /** Open the voice bar on arrival (the old Voice tab's link lands here). */
  startVoice?: boolean;
  className?: string;
}) {
  const voiceMode = useVoiceMode(chat, voice);
  const { open: openVoice } = voiceMode;
  useEffect(() => {
    if (voice && startVoice) openVoice();
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voice, startVoice]);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const last = chat.messages[chat.messages.length - 1];

  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages, last]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const retry = (index: number) => {
    const question = chat.messages[index - 1];
    if (question?.role === "user") void chat.send(question.text);
  };

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <div
        ref={scroller}
        onScroll={onScroll}
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto"
        data-testid="chat-thread"
      >
        <div className={cn("mx-auto flex w-full max-w-3xl flex-col gap-6", compact ? "p-3" : "px-4 py-6")}>
          {chat.messages.length === 0 && !chat.loading
            ? (empty ?? (
                <Suggestions audience={chat.audience} onPick={(q) => void chat.send(q)} compact={compact} />
              ))
            : chat.messages.map((m, i) =>
                m.role === "user" ? (
                  <UserBubble key={m.id} message={m} />
                ) : (
                  <AssistantBubble
                    key={m.id}
                    message={m}
                    onRetry={() => retry(i)}
                    showTimeline={!compact}
                    checkHref={checkHref}
                  />
                ),
              )}
          {chat.loading && <p className="text-sm text-muted-foreground">Loading the conversation…</p>}
        </div>
      </div>
      <div className={cn("mx-auto w-full max-w-3xl", compact ? "p-2" : "px-4 pb-4")}>
        {voice && <VoiceBar voice={voiceMode} />}
        <Composer
          onSend={(t) => void chat.send(t)}
          onStop={chat.stop}
          busy={chat.busy}
          placeholder={placeholder}
          autoFocus={!compact}
          leading={
            showAudience ? (
              <AudienceSwitch value={chat.audience} onChange={chat.setAudience} disabled={chat.busy} />
            ) : null
          }
          footer={footer}
          trailing={
            voice
              ? (append) => (
                  <>
                    <DictationButton onText={append} />
                    <VoiceModeButton voice={voiceMode} />
                  </>
                )
              : undefined
          }
        />
      </div>
    </div>
  );
}

function Suggestions({
  audience,
  onPick,
  compact,
}: {
  audience: Audience;
  onPick: (question: string) => void;
  compact: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center text-center", compact ? "gap-3 py-4" : "gap-5 py-10")}>
      <span className="relative grid place-items-center" aria-hidden>
        <span className="absolute size-16 rounded-full bg-[radial-gradient(closest-side,rgb(99_102_241/0.35),transparent)] blur-md" />
        <LogoMark className={cn("relative", compact ? "size-9" : "size-11")} />
      </span>
      <div>
        <h2 className={cn("font-semibold tracking-tight", compact ? "text-base" : "text-2xl")}>
          How can I help today?
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Answers come from medical literature, guidelines and drug labels — every fact linked to its
          source.
        </p>
      </div>
      <div className={cn("grid w-full gap-2 text-left", compact ? "grid-cols-1" : "sm:grid-cols-2")}>
        {SUGGESTIONS[audience].map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-xl border bg-card px-3.5 py-3 text-sm shadow-[0_10px_24px_-20px_rgb(30_27_75/0.6)] transition-all hover:-translate-y-px hover:border-primary/40 hover:bg-accent/60"
            data-testid="chat-suggestion"
          >
            {q}
          </button>
        ))}
      </div>
      <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
        <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
        Information, not a diagnosis. In an emergency call 112 or 108 (India).
      </p>
    </div>
  );
}
