"use client";

import {
  BarChart3,
  Bell,
  BookMarked,
  Clock,
  FolderOpen,
  GraduationCap,
  History,
  LayoutList,
  Library,
  MessageSquarePlus,
  MessagesSquare,
  Mic,
  Moon,
  Settings2,
  Stethoscope,
  Sun,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useDocuments, useSessions } from "@/hooks/use-api";

interface PaletteState {
  open: () => void;
  close: () => void;
  isOpen: boolean;
}

const PaletteContext = createContext<PaletteState | null>(null);

export function useCommandPalette(): PaletteState {
  const ctx = useContext(PaletteContext);
  if (!ctx) throw new Error("useCommandPalette must be used inside CommandPaletteProvider");
  return ctx;
}

/**
 * ⌘K / Ctrl+K from anywhere. Every major screen is reachable from here and
 * the two most common "jump to" targets — a recent session and a library
 * document — are searched live from the same input.
 */
export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [isOpen, setOpen] = useState(false);
  const open = useCallback(() => setOpen(true), []);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const value = useMemo(() => ({ open, close, isOpen }), [open, close, isOpen]);
  return (
    <PaletteContext.Provider value={value}>
      {children}
      <Palette open={isOpen} onOpenChange={setOpen} />
    </PaletteContext.Provider>
  );
}

function Palette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { role } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const [search, setSearch] = useState("");

  const sessions = useSessions({ limit: 8, mine: true });
  const term = search.trim();
  const docs = useDocuments({ search: term, limit: 6 }, term.length >= 3);

  const go = useCallback(
    (href: string) => {
      onOpenChange(false);
      setSearch("");
      router.push(href);
    },
    [onOpenChange, router],
  );

  const isOwner = role === "owner";
  const canSeeDashboard = role === "owner" || role === "clinician";

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Command palette"
      description="Jump to a screen, a session, or a document"
      className="max-w-xl"
    >
      <Command shouldFilter={true}>
        <CommandInput
          placeholder="Where to? Type a screen, a session, or a paper…"
          value={search}
          onValueChange={setSearch}
          autoFocus
        />
        <CommandList className="max-h-[60vh]">
          <CommandEmpty>Nothing matches. Try a screen name or a paper title.</CommandEmpty>

          <CommandGroup heading="Actions">
            <CommandItem onSelect={() => go("/app")} value="new query ask">
              <MessageSquarePlus /> New query
              <CommandShortcut>N</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={() => go("/app/chat")} value="chat assistant conversation ask">
              <MessagesSquare /> Chat with the assistant
            </CommandItem>
            <CommandItem onSelect={() => go("/app/treatment")} value="treatment symptom check prescription medicine dose">
              <Stethoscope /> Symptom check and treatment
            </CommandItem>
            <CommandItem onSelect={() => go("/app/learn")} value="learn specialties mbbs pg teaching">
              <GraduationCap /> Learn by specialty
            </CommandItem>
            <CommandItem onSelect={() => go("/app/voice")} value="voice mode speak talk microphone">
              <Mic /> Voice mode
            </CommandItem>
            <CommandItem
              onSelect={() => {
                setTheme(resolvedTheme === "dark" ? "light" : "dark");
                onOpenChange(false);
              }}
              value="toggle theme dark mode light mode"
            >
              {resolvedTheme === "dark" ? <Sun /> : <Moon />}
              Switch to {resolvedTheme === "dark" ? "light" : "dark"} mode
            </CommandItem>
          </CommandGroup>

          <CommandSeparator />
          <CommandGroup heading="Go to">
            <CommandItem onSelect={() => go("/app/sessions")} value="sessions conversations threads">
              <LayoutList /> Sessions
            </CommandItem>
            <CommandItem onSelect={() => go("/app/history")} value="history past queries trace">
              <History /> History
            </CommandItem>
            <CommandItem onSelect={() => go("/app/library")} value="library corpus documents papers">
              <Library /> Library
            </CommandItem>
            <CommandItem onSelect={() => go("/app/binders")} value="binders collections evidence">
              <BookMarked /> Evidence Binders
            </CommandItem>
            <CommandItem onSelect={() => go("/app/notifications")} value="notifications updates living answers">
              <Bell /> Notifications
            </CommandItem>
            {canSeeDashboard && (
              <CommandItem onSelect={() => go("/app/dashboard")} value="dashboard analytics metrics usage cost">
                <BarChart3 /> Dashboard
              </CommandItem>
            )}
            {isOwner && (
              <CommandItem onSelect={() => go("/app/admin")} value="admin members roles invites uploads plan settings">
                <Settings2 /> Admin
              </CommandItem>
            )}
          </CommandGroup>

          {sessions.data && sessions.data.sessions.length > 0 && (
            <>
              <CommandSeparator />
              <CommandGroup heading="Recent sessions">
                {sessions.data.sessions.map((s) => (
                  <CommandItem
                    key={s.id}
                    value={`session ${s.title ?? "untitled"} ${s.id}`}
                    onSelect={() => go(`/app/sessions/${s.id}`)}
                  >
                    <Clock />
                    <span className="truncate">{s.title ?? "Untitled session"}</span>
                    <CommandShortcut>{s.query_count} turn{s.query_count === 1 ? "" : "s"}</CommandShortcut>
                  </CommandItem>
                ))}
              </CommandGroup>
            </>
          )}

          {docs.data && docs.data.documents.length > 0 && (
            <>
              <CommandSeparator />
              <CommandGroup heading="Library">
                {docs.data.documents.map((d) => (
                  <CommandItem
                    key={d.id}
                    value={`doc ${d.title} ${d.id}`}
                    onSelect={() => go(`/app/library/${d.id}`)}
                  >
                    <FolderOpen />
                    <span className="truncate">{d.title}</span>
                    {d.publication_date && (
                      <CommandShortcut>{d.publication_date.slice(0, 4)}</CommandShortcut>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            </>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
