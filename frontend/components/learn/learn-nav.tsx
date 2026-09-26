"use client";

import { BookOpen, FileText, GraduationCap, NotebookPen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/app/learn", label: "Subjects", icon: BookOpen, match: (p: string) => isSubjects(p) },
  { href: "/app/learn/tutor", label: "AI Tutor", icon: GraduationCap, match: (p: string) => p.startsWith("/app/learn/tutor") },
  { href: "/app/learn/notes", label: "Summarize a note", icon: NotebookPen, match: (p: string) => p.startsWith("/app/learn/notes") },
  { href: "/app/learn/papers", label: "Ask a paper", icon: FileText, match: (p: string) => p.startsWith("/app/learn/papers") },
] as const;

const TOOLS = new Set(["tutor", "notes", "papers"]);

/** The subject list and every subject's own page ("/app/learn/cardiology"). */
function isSubjects(path: string): boolean {
  const rest = path.replace(/^\/app\/learn\/?/, "").split("/")[0] ?? "";
  return !TOOLS.has(rest);
}

/**
 * Learn's four tools, one tap apart. On a phone the row scrolls sideways
 * inside itself, never the page.
 */
export function LearnNav() {
  const pathname = usePathname() ?? "/app/learn";
  return (
    <nav aria-label="Learn" className="border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-3 md:px-6 [scrollbar-width:none]">
        {TABS.map(({ href, label, icon: Icon, match }) => {
          const active = match(pathname);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2.5 text-sm transition-colors",
                active
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
              data-testid={`learn-tab-${href.split("/").pop()}`}
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
