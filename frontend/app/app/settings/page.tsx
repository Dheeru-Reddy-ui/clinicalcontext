"use client";

import {
  AudioLines,
  Bell,
  Building2,
  ChevronLeft,
  ChevronRight,
  Gauge,
  GraduationCap,
  Info,
  Keyboard,
  KeyRound,
  Library,
  LockKeyhole,
  MessagesSquare,
  Palette,
  Share2,
  ShieldCheck,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, type ComponentType } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { AboutSection, ShortcutsSection } from "@/components/settings/help";
import {
  ApiKeysSection,
  MembersSection,
  OrganisationSection,
  PlanSection,
  PrivateLibrarySection,
  SharingSection,
} from "@/components/settings/organisation";
import {
  AppearanceSection,
  AssistantSection,
  LearnSection,
  NotificationsSection,
  PrivacySection,
  ProfileSection,
  SecuritySection,
  VoiceSection,
} from "@/components/settings/personal";
import { cn } from "@/lib/utils";

/**
 * Settings: everything a person can set about themselves and — owners —
 * about their organisation, grouped the way the product is used.
 *
 * On a laptop the sections sit in a list beside the open one. On a phone
 * the list is the page, and a section opens full width with a way back —
 * the pattern a phone's own settings use. Every section has a URL
 * (?section=voice), so a link can open it directly.
 */

interface Section {
  id: string;
  label: string;
  hint: string;
  icon: LucideIcon;
  group: string;
  ownerOnly?: boolean;
  Render: ComponentType;
}

const SECTIONS: Section[] = [
  { id: "profile", label: "Profile", hint: "Name, specialty, organisation", icon: UserRound, group: "You", Render: ProfileSection },
  { id: "security", label: "Sign-in & security", hint: "Password, signed-in devices", icon: LockKeyhole, group: "You", Render: SecuritySection },
  { id: "appearance", label: "Appearance", hint: "Theme, text size", icon: Palette, group: "You", Render: AppearanceSection },
  { id: "assistant", label: "Assistant", hint: "Who answers are written for, sources", icon: MessagesSquare, group: "Using ClinicalContext", Render: AssistantSection },
  { id: "voice", label: "Voice", hint: "Read-aloud voice, pace, microphone", icon: AudioLines, group: "Using ClinicalContext", Render: VoiceSection },
  { id: "learn", label: "Learn", hint: "Subjects, depth, your subjects", icon: GraduationCap, group: "Using ClinicalContext", Render: LearnSection },
  { id: "notifications", label: "Notifications", hint: "Weekly digest, answer updates", icon: Bell, group: "Using ClinicalContext", Render: NotificationsSection },
  { id: "privacy", label: "Privacy & data", hint: "What is kept, download, delete", icon: ShieldCheck, group: "Using ClinicalContext", Render: PrivacySection },
  { id: "organisation", label: "Organisation", hint: "Workspace, plan, your role", icon: Building2, group: "Workspace", Render: OrganisationSection },
  { id: "members", label: "Members & invites", hint: "Who can do what", icon: Users, group: "Workspace", ownerOnly: true, Render: MembersSection },
  { id: "sharing", label: "Public sharing", hint: "Answer links", icon: Share2, group: "Workspace", ownerOnly: true, Render: SharingSection },
  { id: "api", label: "API keys", hint: "Server-to-server access", icon: KeyRound, group: "Workspace", ownerOnly: true, Render: ApiKeysSection },
  { id: "library", label: "Private library", hint: "Your organisation's PDFs", icon: Library, group: "Workspace", ownerOnly: true, Render: PrivateLibrarySection },
  { id: "plan", label: "Plan & usage", hint: "Limits and spend", icon: Gauge, group: "Workspace", ownerOnly: true, Render: PlanSection },
  { id: "shortcuts", label: "Keyboard shortcuts", hint: "For a laptop", icon: Keyboard, group: "Help", Render: ShortcutsSection },
  { id: "about", label: "About", hint: "What it is, and is not", icon: Info, group: "Help", Render: AboutSection },
];

function SettingsView() {
  const params = useSearchParams();
  const { role } = useAuth();
  const visible = SECTIONS.filter((s) => !s.ownerOnly || role === "owner");
  const requested = visible.find((s) => s.id === params.get("section")) ?? null;
  // A laptop always shows one section; a phone shows the list until one is chosen.
  const shown = requested ?? visible[0];
  const groups = [...new Set(visible.map((s) => s.group))];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-4 md:p-6" data-testid="settings">
      <header className={cn(requested && "hidden lg:block")}>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Your account, how the assistant works for you, and your workspace.</p>
      </header>

      <div className="lg:grid lg:grid-cols-[15rem_minmax(0,1fr)] lg:items-start lg:gap-8">
        <nav
          aria-label="Settings sections"
          className={cn("flex flex-col gap-5 lg:sticky lg:top-6", requested ? "hidden lg:flex" : "flex")}
        >
          {groups.map((group) => (
            <div key={group}>
              <p className="px-1 pb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{group}</p>
              <ul className="overflow-hidden rounded-2xl border bg-card lg:rounded-none lg:border-0 lg:bg-transparent">
                {visible
                  .filter((s) => s.group === group)
                  .map((section) => {
                    const Icon = section.icon;
                    const active = shown?.id === section.id;
                    return (
                      <li key={section.id} className="border-b last:border-b-0 lg:border-0">
                        <Link
                          href={`/app/settings?section=${section.id}`}
                          aria-current={active ? "page" : undefined}
                          data-testid={`settings-nav-${section.id}`}
                          className={cn(
                            "flex items-center gap-3 px-3 py-3 text-sm transition-colors lg:rounded-lg lg:px-2.5 lg:py-1.5",
                            active
                              ? "lg:bg-accent lg:font-medium lg:text-accent-foreground"
                              : "hover:bg-muted/60 lg:text-muted-foreground lg:hover:text-foreground",
                          )}
                        >
                          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-foreground lg:size-auto lg:bg-transparent lg:text-inherit">
                            <Icon className="size-4" aria-hidden />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block">{section.label}</span>
                            <span className="block truncate text-xs text-muted-foreground lg:hidden">{section.hint}</span>
                          </span>
                          <ChevronRight className="size-4 text-muted-foreground lg:hidden" aria-hidden />
                        </Link>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))}
        </nav>

        {shown && (
          <div className={cn("min-w-0 flex-col gap-4", requested ? "flex" : "hidden lg:flex")}>
            <Link
              href="/app/settings"
              className="-ml-1 inline-flex w-fit items-center gap-1 rounded-md px-1 py-1 text-sm text-muted-foreground hover:text-foreground lg:hidden"
            >
              <ChevronLeft className="size-4" aria-hidden /> Settings
            </Link>
            <h2 className="text-lg font-semibold tracking-tight" data-testid="settings-section-title">
              {shown.label}
            </h2>
            <shown.Render />
          </div>
        )}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={null}>
      <SettingsView />
    </Suspense>
  );
}
