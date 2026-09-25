"use client";

import { Maximize2, MessageCircle, MessageSquarePlus, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LogoMark } from "@/components/brand/logo";
import { ChatPanel } from "@/components/chat/chat-panel";
import { Button, buttonVariants } from "@/components/ui/button";
import { useChat, type UseChatOptions } from "@/hooks/use-chat";
import { OPEN_ASSISTANT_EVENT } from "@/lib/assistant-events";
import { cn } from "@/lib/utils";

/**
 * The assistant on every page: a round button bottom-right that opens a
 * compact chat over whatever the person is doing, and a way to carry the
 * conversation to the full Chat page. The website's version (`mode:
 * "public"`) keeps nothing; the app's is saved like any conversation.
 */
export function AssistantLauncher({
  mode = "app",
  hideOn = [],
  fullHref,
  checkHref,
}: {
  mode?: UseChatOptions["mode"];
  hideOn?: string[];
  fullHref?: string;
  checkHref?: string;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const chat = useChat({ mode, kind: "chat" });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Other parts of the page can open it (lib/assistant-events).
  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_ASSISTANT_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ASSISTANT_EVENT, onOpen);
  }, []);

  if (hideOn.some((p) => pathname === p || pathname.startsWith(`${p}/`))) return null;

  return (
    <>
      {open && (
        <div
          role="dialog"
          aria-label="Health assistant"
          className="fixed inset-0 z-50 flex flex-col bg-background sm:inset-auto sm:right-4 sm:bottom-20 sm:h-[min(640px,calc(100vh-7rem))] sm:w-[400px] sm:rounded-xl sm:border sm:shadow-2xl"
          data-testid="assistant-panel"
        >
          <header className="flex items-center gap-2 border-b px-3 py-2">
            <LogoMark className="size-7" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-4">Health assistant</p>
              <p className="text-[11px] text-muted-foreground">Answers with sources · not a diagnosis</p>
            </div>
            {chat.messages.length > 0 && (
              <Button size="icon-sm" variant="ghost" onClick={chat.reset} aria-label="New conversation">
                <MessageSquarePlus />
              </Button>
            )}
            {fullHref && chat.sessionId && (
              <Link
                href={`${fullHref}?session=${chat.sessionId}`}
                aria-label="Open in the full chat"
                className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
                onClick={() => setOpen(false)}
              >
                <Maximize2 />
              </Link>
            )}
            <Button size="icon-sm" variant="ghost" onClick={() => setOpen(false)} aria-label="Close assistant">
              <X />
            </Button>
          </header>
          <ChatPanel
            chat={chat}
            compact
            checkHref={checkHref}
            voice={mode === "app"}
            placeholder="Ask a health question…"
          />
        </div>
      )}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close health assistant" : "Open health assistant"}
        aria-expanded={open}
        data-testid="assistant-launcher"
        className={cn(
          "fixed right-4 bottom-4 z-50 grid size-13 place-items-center rounded-full bg-[linear-gradient(135deg,#6366f1_0%,#7c3aed_100%)] text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.35),0_14px_30px_-10px_rgb(99_102_241/0.85)] ring-1 ring-white/20 transition-transform hover:scale-105 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          open && "max-sm:hidden",
        )}
      >
        {open ? <X className="size-5" /> : <MessageCircle className="size-6" />}
      </button>
    </>
  );
}
