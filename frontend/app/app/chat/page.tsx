"use client";

import { MessageSquarePlus, MessagesSquare, PanelLeft, Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { Button } from "@/components/ui/button";
import { useAssistantStatus, useChatSessions } from "@/hooks/use-api";
import { useChat } from "@/hooks/use-chat";
import { warmApi } from "@/lib/signup";
import { formatDate } from "@/lib/text";
import { cn } from "@/lib/utils";

function StartFromUrl({
  onStart,
}: {
  onStart: (q: string | null, audience: string | null, session: string | null) => void;
}) {
  const params = useSearchParams();
  const q = params.get("q");
  const audience = params.get("audience");
  const session = params.get("session");
  useEffect(() => onStart(q, audience, session), [q, audience, session, onStart]);
  return null;
}

/**
 * The chat assistant: a conversation with memory that answers any health or
 * medical question from evidence — for a patient, a doctor, or a student —
 * with the conversations kept on the left.
 */
export default function ChatPage() {
  const chat = useChat({ kind: "chat" });
  const sessions = useChatSessions("chat");
  const status = useAssistantStatus();
  const [listOpen, setListOpen] = useState(false);
  const handled = useRef(false);

  useEffect(warmApi, []);

  // ?q= starts a conversation (from the Treatment and Learn tabs), and
  // ?session= continues one (from the floating assistant).
  const { open, send, setAudience, ready } = chat;
  const startFromUrl = useCallback(
    (q: string | null, audience: string | null, session: string | null) => {
      if (handled.current || !ready) return;
      handled.current = true;
      if (audience === "patient" || audience === "clinician" || audience === "student") {
        setAudience(audience);
      }
      if (session) void open(session);
      else if (q) void send(q);
    },
    [open, send, setAudience, ready],
  );

  return (
    <div className="flex h-[calc(100dvh-3rem)] min-h-0 md:h-dvh">
      <Suspense fallback={null}>
        <StartFromUrl onStart={startFromUrl} />
      </Suspense>
      <aside
        className={cn(
          "w-64 shrink-0 flex-col border-r bg-sidebar/40",
          listOpen ? "fixed inset-y-0 left-0 z-40 flex bg-background md:static" : "hidden lg:flex",
        )}
        aria-label="Conversations"
      >
        <div className="flex items-center justify-between gap-2 border-b p-3">
          <h1 className="text-sm font-semibold">Chats</h1>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              chat.reset();
              setListOpen(false);
            }}
            data-testid="chat-new"
          >
            <MessageSquarePlus /> New chat
          </Button>
        </div>
        <nav className="scrollbar-thin flex-1 overflow-y-auto p-2" data-testid="chat-list">
          {sessions.data?.length === 0 && (
            <p className="px-2 py-4 text-xs text-muted-foreground">Your conversations will appear here.</p>
          )}
          {sessions.data?.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                void chat.open(s.id);
                setListOpen(false);
              }}
              className={cn(
                "flex w-full flex-col items-start rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted",
                chat.sessionId === s.id && "bg-muted font-medium",
              )}
            >
              <span className="line-clamp-2">{s.title ?? "Untitled"}</span>
              <span className="text-[11px] text-muted-foreground">
                {formatDate(s.updated_at, { month: "short", day: "numeric" })} · {s.turns}{" "}
                {s.turns === 1 ? "message" : "messages"}
              </span>
            </button>
          ))}
        </nav>
      </aside>
      {listOpen && (
        <button
          type="button"
          aria-label="Close conversations"
          className="fixed inset-0 z-30 bg-black/30 lg:hidden"
          onClick={() => setListOpen(false)}
        />
      )}

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-4 py-2.5">
          <Button
            size="icon-sm"
            variant="ghost"
            className="lg:hidden"
            onClick={() => setListOpen(true)}
            aria-label="Show conversations"
          >
            <PanelLeft />
          </Button>
          <MessagesSquare className="size-4 text-muted-foreground" aria-hidden />
          <h2 className="text-sm font-medium">Chat</h2>
          {status.data && (
            <span
              className="ml-auto inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground"
              title={status.data.message}
              data-testid="assistant-mode"
            >
              <Sparkles className="size-3" aria-hidden />
              {status.data.llm_available
                ? `AI writer: ${status.data.providers.join(", ")}`
                : "Quoting sources (no AI writer configured)"}
            </span>
          )}
        </header>
        <ChatPanel
          chat={chat}
          dictation
          footer="ClinicalContext gives information from medical sources, not a diagnosis. It can be wrong — check important decisions with a doctor."
        />
      </section>
    </div>
  );
}
