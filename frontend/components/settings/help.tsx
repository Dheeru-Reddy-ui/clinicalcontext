"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { SettingRow, SettingsCard } from "@/components/settings/ui";
import { Kbd } from "@/components/ui/kbd";

const SHORTCUTS: ReadonlyArray<{ keys: string[]; action: string; where: string }> = [
  { keys: ["⌘", "K"], action: "Jump to any page or action (Ctrl K on Windows)", where: "Everywhere" },
  { keys: ["Enter"], action: "Send the question", where: "Chat, Evidence search" },
  { keys: ["Shift", "Enter"], action: "New line", where: "Chat, Evidence search" },
  { keys: ["↑", "↓"], action: "Move through suggested questions", where: "Evidence search" },
  { keys: ["Tab"], action: "Use the highlighted suggestion", where: "Evidence search" },
  { keys: ["]"], action: "Next source (or →)", where: "An open source" },
  { keys: ["["], action: "Previous source (or ←)", where: "An open source" },
  { keys: ["Esc"], action: "Close the source, the suggestions or the assistant", where: "Everywhere" },
];

export function ShortcutsSection() {
  return (
    <SettingsCard title="Keys" description="For a laptop, or a tablet or phone with a keyboard.">
      {SHORTCUTS.map((shortcut) => (
        <SettingRow
          key={shortcut.action}
          label={shortcut.action}
          description={shortcut.where}
          control={
            <span className="flex items-center gap-1">
              {shortcut.keys.map((key) => (
                <Kbd key={key}>{key}</Kbd>
              ))}
            </span>
          }
        />
      ))}
    </SettingsCard>
  );
}

export function AboutSection() {
  const commit = process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA;
  return (
    <div className="flex flex-col gap-4">
      <SettingsCard title="ClinicalContext">
        <SettingRow
          label="What it is"
          description="Evidence-grounded answers to health and clinical questions, for patients, doctors and students — every claim linked to its source."
        />
        <SettingRow
          label="What it is not"
          description="A diagnosis or a substitute for a doctor. It supports decisions; it does not make them."
        />
        <SettingRow
          label="In an emergency"
          description="Call 112, or 108 for an ambulance in India. For mental health support, Tele-MANAS: 14416."
        />
        {commit && <SettingRow label="Version" control={<code className="font-mono text-xs">{commit.slice(0, 7)}</code>} />}
      </SettingsCard>
      <SettingsCard title="How it is measured">
        {[
          { href: "/methodology", label: "Methodology", body: "Every number, read live from the evaluation runs." },
          { href: "/methodology#limitations", label: "Limitations", body: "What it gets wrong, and what it is not." },
          { href: "/health", label: "System health", body: "Whether the server, database and cache are up." },
        ].map((item) => (
          <Link key={item.href} href={item.href} className="group flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
            <span>
              <span className="block text-sm font-medium group-hover:text-primary">{item.label}</span>
              <span className="block text-xs text-muted-foreground">{item.body}</span>
            </span>
            <ArrowRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </Link>
        ))}
      </SettingsCard>
    </div>
  );
}
