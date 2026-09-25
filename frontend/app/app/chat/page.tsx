"use client";

import { useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus, MessagesSquare, PanelLeft, Sparkles, Trash2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ChatPanel } from "@/components/chat/chat-panel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAssistantStatus, useChatSessions, useToken } from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import { useChat } from "@/hooks/use-chat";
import { warmApi } from "@/lib/signup";
import { formatDate } from "@/lib/text";
import { cn } from "@/lib/utils";

function StartFromUrl({
  onStart,
}: {
  onStart: (
    q: string | null,
    audience: string | null,
    session: string | null,
    voice: string | null,
  ) => void;
}) {
  const params = useSearchParams();
  const q = params.get("q");
  const audience = params.get("audience");
  const session = params.get("session");
  const voice = params.get("voice");
  useEffect(() => onStart(q, audience, session, voice), [q, audience, session, voice, onStart]);
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
  const [startVoice, setStartVoice] = useState(false);
  const token = useToken();
  const queryClient = useQueryClient();
  const [doomed, setDoomed] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(warmApi, []);

  const deleteConversation = async () => {
    if (!doomed) return;
    setDeleting(true);
    try {
      const out = await api.deleteConversation(token, doomed.id);
      if (out.kept) {
        toast.message("This conversation was kept", {
          description: "An answer in it is saved to a binder, shared, or followed for updates — remove it from there first.",
        });
      } else {
        toast.success("Conversation deleted.");
        if (chat.sessionId === doomed.id) chat.reset();
      }
      await queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      setDoomed(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't delete the conversation.");
    } finally {
      setDeleting(false);
    }
  };

  // ?q= starts a conversation (from the Treatment and Learn tabs), and
  // ?session= continues one (from the floating assistant).
  const { open, send, setAudience, ready } = chat;
  const startFromUrl = useCallback(
    (q: string | null, audience: string | null, session: string | null, voice: string | null) => {
      if (handled.current || !ready) return;
      handled.current = true;
      if (voice) setStartVoice(true);
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
            <div
              key={s.id}
              className={cn(
                "group flex items-start gap-1 rounded-md transition-colors hover:bg-muted",
                chat.sessionId === s.id && "bg-muted",
              )}
            >
              <button
                type="button"
                onClick={() => {
                  void chat.open(s.id);
                  setListOpen(false);
                }}
                className={cn(
                  "flex min-w-0 flex-1 flex-col items-start px-2.5 py-2 text-left text-sm",
                  chat.sessionId === s.id && "font-medium",
                )}
              >
                <span className="line-clamp-2">{s.title ?? "Untitled"}</span>
                <span className="text-[11px] text-muted-foreground">
                  {formatDate(s.updated_at, { month: "short", day: "numeric" })} · {s.turns}{" "}
                  {s.turns === 1 ? "message" : "messages"}
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                className="mt-1.5 mr-1 opacity-100 group-hover:opacity-100 focus-visible:opacity-100 lg:opacity-0"
                onClick={() => setDoomed({ id: s.id, title: s.title ?? "Untitled" })}
                aria-label={`Delete conversation: ${s.title ?? "Untitled"}`}
                data-testid="chat-delete"
              >
                <Trash2 />
              </Button>
            </div>
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
              {status.data.llm_available ? (
                <>
                  <span className="sm:hidden">AI writer on</span>
                  <span className="hidden sm:inline">AI writer: {status.data.providers.join(", ")}</span>
                </>
              ) : (
                <>
                  <span className="sm:hidden">Quoting sources</span>
                  <span className="hidden sm:inline">Quoting sources (no AI writer configured)</span>
                </>
              )}
            </span>
          )}
        </header>
        <ChatPanel
          chat={chat}
          voice
          startVoice={startVoice}
          footer="ClinicalContext gives information from medical sources, not a diagnosis. It can be wrong — check important decisions with a doctor."
        />
      </section>
      <Dialog open={doomed !== null} onOpenChange={(open) => !open && setDoomed(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              &ldquo;{doomed?.title}&rdquo; and its answers are deleted for good. It stays if an answer in it is
              saved to a binder, shared, or followed for updates.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDoomed(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void deleteConversation()} disabled={deleting} data-testid="chat-delete-confirm">
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
