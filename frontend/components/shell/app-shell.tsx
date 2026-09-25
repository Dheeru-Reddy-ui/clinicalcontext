"use client";

import {
  BarChart3,
  Bell,
  BookMarked,
  History,
  LayoutList,
  Library,
  GraduationCap,
  LogOut,
  Menu,
  MessageSquarePlus,
  MessagesSquare,
  Monitor,
  Moon,
  Search,
  Settings2,
  Stethoscope,
  Sun,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState, type ReactNode } from "react";

import { LogoMark } from "@/components/brand/logo";
import { AssistantLauncher } from "@/components/chat/assistant-launcher";
import { useAuth } from "@/components/providers/auth-provider";
import { useCommandPalette } from "@/components/shell/command-palette";
import { OfflineBanner } from "@/components/shell/offline-banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useNotifications } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof MessageSquarePlus;
  exact?: boolean;
  roles?: ReadonlyArray<"owner" | "clinician" | "viewer">;
}

const NAV: NavItem[] = [
  { href: "/app/chat", label: "Chat", icon: MessagesSquare },
  { href: "/app/treatment", label: "Treatment", icon: Stethoscope },
  { href: "/app/learn", label: "Learn", icon: GraduationCap },
  { href: "/app", label: "Evidence search", icon: MessageSquarePlus, exact: true },
  { href: "/app/sessions", label: "Sessions", icon: LayoutList },
  { href: "/app/history", label: "History", icon: History },
  { href: "/app/library", label: "Library", icon: Library },
  { href: "/app/binders", label: "Binders", icon: BookMarked },
  { href: "/app/dashboard", label: "Dashboard", icon: BarChart3, roles: ["owner", "clinician"] },
  { href: "/app/settings", label: "Settings", icon: Settings2 },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { org, role, me, signOut } = useAuth();
  const palette = useCommandPalette();
  const [menuOpen, setMenuOpen] = useState(false);

  const visibleNav = NAV.filter((item) => !item.roles || (role && item.roles.includes(role)));

  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-sm focus:bg-background focus:px-3 focus:py-2 focus:outline-2"
      >
        Skip to content
      </a>

      <aside
        className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex"
        aria-label="Primary"
      >
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <LogoMark className="size-6" />
          <span className="font-semibold tracking-tight">ClinicalContext</span>
        </div>

        <Button
          variant="outline"
          size="sm"
          className="mx-3 mt-3 justify-between text-muted-foreground"
          onClick={palette.open}
          aria-keyshortcuts="Meta+K Control+K"
        >
          <span className="inline-flex items-center gap-2">
            <Search className="size-3.5" /> Jump to…
          </span>
          <Kbd>⌘K</Kbd>
        </Button>

        <nav className="mt-3 flex flex-1 flex-col gap-0.5 px-2" aria-label="Sections">
          {visibleNav.map((item) => {
            const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-sm px-2.5 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t p-2">
          <div className="flex items-center gap-1">
            <NotificationBell />
            <ThemeToggle />
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="ghost" size="sm" className="ml-auto max-w-[9rem] justify-start" />
                }
              >
                <span className="truncate text-xs">{me?.full_name ?? me?.email ?? "Account"}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuGroup>
                <DropdownMenuLabel className="flex flex-col gap-0.5">
                  <span className="truncate font-medium">{org?.name}</span>
                  <span className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
                    {me?.email}
                    {role && (
                      <Badge variant="secondary" className="capitalize">
                        {role}
                      </Badge>
                    )}
                  </span>
                </DropdownMenuLabel>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem render={<Link href="/app/settings" />}>
                  <Settings2 /> Settings
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => void signOut()}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center justify-between border-b px-2 md:hidden">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
            aria-expanded={menuOpen}
            data-testid="mobile-menu"
          >
            <Menu className="size-4" />
          </Button>
          <Link href="/app/chat" className="inline-flex items-center gap-2 font-semibold tracking-tight">
            <LogoMark className="size-6" />
            ClinicalContext
          </Link>
          <Button variant="ghost" size="sm" onClick={palette.open} aria-label="Open command palette">
            <Search className="size-4" />
          </Button>
        </header>
        {menuOpen && (
          <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-label="Menu">
            <button
              type="button"
              aria-label="Close menu"
              className="absolute inset-0 bg-black/40"
              onClick={() => setMenuOpen(false)}
            />
            <nav
              className="absolute inset-y-0 left-0 flex w-64 flex-col gap-0.5 bg-sidebar p-2 shadow-xl"
              aria-label="Sections"
            >
              <p className="flex items-center gap-2 px-2.5 py-3 font-semibold tracking-tight">
                <LogoMark className="size-6" /> ClinicalContext
              </p>
              {visibleNav.map((item) => {
                const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMenuOpen(false)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm",
                      active ? "bg-sidebar-accent font-medium" : "text-muted-foreground",
                    )}
                  >
                    <Icon className="size-4" aria-hidden />
                    {item.label}
                  </Link>
                );
              })}
              <button
                type="button"
                onClick={() => void signOut()}
                className="mt-auto flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm text-muted-foreground"
              >
                <LogOut className="size-4" aria-hidden /> Sign out
              </button>
            </nav>
          </div>
        )}
        <OfflineBanner />
        {/* Bottom room so the floating assistant never covers a page's last button. */}
        <main id="main" className={cn("min-w-0 flex-1", !pathname.startsWith("/app/chat") && "pb-20")} tabIndex={-1}>
          {children}
        </main>
        <AssistantLauncher hideOn={["/app/chat"]} fullHref="/app/chat" checkHref="/app/treatment" />
      </div>
    </div>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // Render a neutral control until mounted so server and client markup agree.
  const current = mounted ? theme ?? "system" : "system";
  const Icon = current === "dark" ? Moon : current === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger
          render={
            <DropdownMenuTrigger
              render={<Button variant="ghost" size="icon-sm" aria-label="Theme" />}
            />
          }
        >
          <Icon className="size-4" />
        </TooltipTrigger>
        <TooltipContent>Theme: {current}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun /> Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor /> System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NotificationBell() {
  const { data } = useNotifications();
  const unread = data?.unread ?? 0;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/app/notifications"
            className="relative inline-flex size-8 items-center justify-center rounded-sm text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
            aria-label={unread ? `${unread} unread notifications` : "Notifications"}
          />
        }
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span
            aria-hidden
            className="absolute right-1 top-1 grid min-w-3.5 place-items-center rounded-full bg-primary px-0.5 font-mono text-[9px] font-bold leading-3.5 text-primary-foreground"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </TooltipTrigger>
      <TooltipContent>{unread ? `${unread} unread` : "No new notifications"}</TooltipContent>
    </Tooltip>
  );
}
