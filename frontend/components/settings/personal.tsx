"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, LogOut, Pause, Play, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { PasswordField } from "@/components/auth/password-field";
import { DigestPreferencesCard } from "@/components/notifications/digest";
import { useAuth } from "@/components/providers/auth-provider";
import { Choice, SavedHint, Segmented, SettingRow, SettingsCard } from "@/components/settings/ui";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { MicCheck, readSavedMicId } from "@/components/voice/mic-check";
import { useSpecialties, useToken } from "@/hooks/use-api";
import {
  usePreferences,
  useUpdatePreferences,
  type Preferences,
  type PreferencesChange,
} from "@/hooks/use-preferences";
import { api } from "@/lib/api-client";
import { createClient } from "@/lib/supabase/client";
import { currentTextSize, setTextSize, TEXT_SIZE_LABELS, TEXT_SIZES, type TextSize } from "@/lib/text-size";
import { isThemeLook, MATCH_DEVICE, THEME_HINTS, THEME_LABELS, THEME_LOOKS, type ThemeLook } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { SpeechQueue } from "@/lib/voice-chat";

/** Save preference changes as they are made, with a quiet "Saved". */
function usePreferenceSaver() {
  const update = useUpdatePreferences();
  const [savedAt, setSavedAt] = useState(0);
  const save = useCallback(
    (change: PreferencesChange) =>
      update.mutate(change, {
        onSuccess: () => setSavedAt(Date.now()),
        onError: (error) =>
          toast.error(error instanceof Error ? `Couldn't save: ${error.message}` : "Couldn't save that change."),
      }),
    [update],
  );
  return { save, savedAt };
}

/** Shown where preferences cannot be stored yet (a server not yet updated). */
function NotStoredYet() {
  return (
    <p className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground" role="note">
      These settings can&rsquo;t be saved on this server yet — they apply as soon as it is updated.
    </p>
  );
}

// -- profile ----------------------------------------------------------------------------

const ROLE_HELP: Record<string, string> = {
  owner: "Manages members, sharing, API keys and the private library.",
  clinician: "Asks questions, saves and shares answers.",
  viewer: "Reads the organisation's answers and binders.",
};

export function ProfileSection() {
  const { me, org, role, refreshMe } = useAuth();
  const token = useToken();
  const specialties = useSpecialties();
  const [name, setName] = useState(me?.full_name ?? "");
  const [specialty, setSpecialty] = useState(me?.specialty ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(me?.full_name ?? "");
    setSpecialty(me?.specialty ?? "");
  }, [me?.full_name, me?.specialty]);

  const dirty = name.trim() !== (me?.full_name ?? "") || specialty.trim() !== (me?.specialty ?? "");

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api.updateProfile(token, { full_name: name.trim(), specialty: specialty.trim() });
      await refreshMe();
      toast.success("Profile saved.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't save the profile.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsCard
      title="Your details"
      description="How you appear to your organisation — on binders, annotations and the members list."
      testId="settings-profile"
    >
      <form onSubmit={(e) => void save(e)} className="grid gap-4 pb-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="profile-name">Full name</Label>
          <Input
            id="profile-name"
            value={name}
            maxLength={120}
            autoComplete="name"
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Dr Asha Rao"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="profile-specialty">Specialty or role</Label>
          <Input
            id="profile-specialty"
            list="profile-specialty-options"
            value={specialty}
            maxLength={120}
            onChange={(e) => setSpecialty(e.target.value)}
            placeholder="e.g. General Medicine, MBBS student"
          />
          <datalist id="profile-specialty-options">
            {["MBBS student", "Intern", "Nurse", "Pharmacist", ...(specialties.data ?? []).map((s) => s.name)].map(
              (option) => (
                <option key={option} value={option} />
              ),
            )}
          </datalist>
        </div>
        <div className="flex justify-end sm:col-span-2">
          <Button type="submit" disabled={!dirty || saving} data-testid="profile-save">
            {saving ? "Saving…" : "Save profile"}
          </Button>
        </div>
      </form>
      <SettingRow label="Email" description="Your sign-in address." control={<span className="text-sm">{me?.email ?? "—"}</span>} />
      <SettingRow
        label="Organisation"
        description={role ? ROLE_HELP[role] : undefined}
        control={
          <span className="flex items-center gap-2 text-sm">
            {org?.name}
            {role && (
              <Badge variant="secondary" className="capitalize">
                {role}
              </Badge>
            )}
          </span>
        }
      />
    </SettingsCard>
  );
}

// -- sign-in & security ------------------------------------------------------------------

export function SecuritySection() {
  const { me } = useAuth();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = confirm.length > 0 && password !== confirm;
  const valid = password.length >= 8 && password === confirm;

  const change = async (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    const { error: failure } = await createClient().auth.updateUser({ password });
    setBusy(false);
    if (failure) {
      setError(failure.message);
      return;
    }
    setPassword("");
    setConfirm("");
    toast.success("Password changed. Use the new one next time you sign in.");
  };

  const signOut = async (scope: "local" | "global") => {
    await createClient().auth.signOut({ scope });
    router.push("/login");
  };

  return (
    <div className="flex flex-col gap-4">
      <SettingsCard
        title="Password"
        description={`Change the password for ${me?.email ?? "your account"}. At least 8 characters.`}
        testId="settings-password"
      >
        <form onSubmit={(e) => void change(e)} className="grid gap-4 sm:grid-cols-2">
          <PasswordField label="New password" value={password} onChange={setPassword} autoComplete="new-password" minLength={8} required={false} />
          <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" minLength={8} required={false} hint={mismatch ? "The two passwords don't match." : undefined} />
          {error && (
            <p className="text-sm text-destructive sm:col-span-2" role="alert">
              {error}
            </p>
          )}
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" disabled={!valid || busy} data-testid="password-save">
              {busy ? "Changing…" : "Change password"}
            </Button>
          </div>
        </form>
      </SettingsCard>

      <SettingsCard title="Sessions" description="Where you are signed in.">
        <SettingRow
          label="Sign out on this device"
          description="Ends this browser's session only."
          control={
            <Button variant="outline" onClick={() => void signOut("local")}>
              <LogOut /> Sign out
            </Button>
          }
        />
        <SettingRow
          label="Sign out everywhere"
          description="Ends every session — phones, laptops and other browsers. Use it if a device was lost or shared."
          control={
            <Button variant="outline" onClick={() => void signOut("global")} data-testid="sign-out-everywhere">
              <LogOut /> Sign out everywhere
            </Button>
          }
        />
      </SettingsCard>
    </div>
  );
}

// -- appearance --------------------------------------------------------------------------

// Each look drawn in its own colours (the tokens in app/globals.css), so the
// choice shows what it gives before it is made.
const LOOK_PREVIEW: Record<ThemeLook, { bg: string; card: string; line: string; accent: string; border: string }> = {
  light: {
    bg: "oklch(0.985 0.003 250)",
    card: "oklch(1 0 0)",
    line: "oklch(0.86 0.01 255)",
    accent: "oklch(0.52 0.2 277)",
    border: "oklch(0.9 0.01 255)",
  },
  dark: {
    bg: "oklch(0.15 0.018 272)",
    card: "oklch(0.19 0.022 272)",
    line: "oklch(0.33 0.02 265)",
    accent: "oklch(0.74 0.13 277)",
    border: "oklch(1 0 0 / 12%)",
  },
  midnight: {
    bg: "oklch(0 0 0)",
    card: "oklch(0.13 0 0)",
    line: "oklch(0.32 0 0)",
    accent: "oklch(0.8 0.12 277)",
    border: "oklch(1 0 0 / 22%)",
  },
};

function LookTiles({ value, onChange }: { value: ThemeLook | null; onChange: (look: ThemeLook) => void }) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const index = value ? THEME_LOOKS.indexOf(value) : 0;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + THEME_LOOKS.length) % THEME_LOOKS.length;
    onChange(THEME_LOOKS[next]!);
    refs.current[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label="Look" onKeyDown={onKeyDown} className="grid grid-cols-3 gap-2 sm:max-w-md" data-testid="theme-looks">
      {THEME_LOOKS.map((look, i) => {
        const selected = look === value;
        const colours = LOOK_PREVIEW[look];
        return (
          <button
            key={look}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={i === index ? 0 : -1}
            onClick={() => onChange(look)}
            className={cn(
              "flex min-w-0 flex-col gap-1.5 rounded-xl border p-1.5 text-left transition-colors",
              selected ? "border-primary ring-2 ring-primary/30" : "hover:border-primary/40",
            )}
            data-testid={`theme-${look}`}
          >
            <span
              aria-hidden
              className="flex h-14 flex-col gap-1 rounded-lg p-1.5"
              style={{ background: colours.bg, border: `1px solid ${colours.border}` }}
            >
              <span className="flex h-full flex-col justify-center gap-1 rounded-md px-1.5" style={{ background: colours.card }}>
                <span className="h-1.5 w-3/5 rounded-full" style={{ background: colours.accent }} />
                <span className="h-1 w-4/5 rounded-full" style={{ background: colours.line }} />
                <span className="h-1 w-2/5 rounded-full" style={{ background: colours.line }} />
              </span>
            </span>
            <span className="px-0.5">
              <span className="block text-xs font-medium">{THEME_LABELS[look]}</span>
              <span className="line-clamp-2 block text-[11px] leading-4 text-muted-foreground">{THEME_HINTS[look]}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function AppearanceSection() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [size, setSize] = useState<TextSize>("default");
  useEffect(() => {
    setMounted(true);
    setSize(currentTextSize());
  }, []);
  // "Match my device" is next-themes' "system": not a look of its own — it
  // shows Light or Dark, whichever the device asks for.
  const matching = !mounted || !isThemeLook(theme);
  const shown: ThemeLook | null = !mounted
    ? null
    : isThemeLook(theme)
      ? theme
      : isThemeLook(resolvedTheme)
        ? resolvedTheme
        : null;

  return (
    <SettingsCard title="On this device" description="How ClinicalContext looks here — each phone or computer keeps its own." testId="settings-appearance">
      <div className="flex flex-col gap-3 pb-3.5">
        <div>
          <p className="text-sm font-medium">Look</p>
          <p className="mt-0.5 text-xs text-pretty text-muted-foreground" data-testid="theme-status">
            {matching
              ? `Following your device — ${shown ? THEME_LABELS[shown] : "Light or Dark"} right now. Pick a look to keep it.`
              : "Three looks, each its own. Midnight is pure black — easiest on a phone at night."}
          </p>
        </div>
        <LookTiles value={shown} onChange={setTheme} />
      </div>
      <SettingRow
        label="Match my device"
        description="Light by day and Dark by night, following your phone or computer's own setting."
        control={
          <Switch
            checked={matching}
            onCheckedChange={(on) => setTheme(on ? MATCH_DEVICE : (shown ?? "light"))}
            aria-label="Match my device"
            data-testid="theme-match-device"
          />
        }
      />
      <SettingRow
        label="Text size"
        description="Makes everything larger on this device — useful on a small phone."
        control={
          <Segmented
            label="Text size"
            value={size}
            choices={TEXT_SIZES.map((value) => ({ value, label: TEXT_SIZE_LABELS[value] }))}
            onChange={(next) => {
              setTextSize(next);
              setSize(next);
            }}
            testId="text-size"
          />
        }
      />
    </SettingsCard>
  );
}

// -- assistant ----------------------------------------------------------------------------

const AUDIENCES: ReadonlyArray<Choice<Preferences["audience"]>> = [
  { value: "patient", label: "Patient", hint: "Plain language" },
  { value: "clinician", label: "Doctor", hint: "Clinician-level, with graded sources" },
  { value: "student", label: "Student", hint: "Explains the reasoning" },
];

export function AssistantSection() {
  const { preferences, available } = usePreferences();
  const { save, savedAt } = usePreferenceSaver();
  return (
    <SettingsCard
      title="Answers"
      description="How the chat assistant answers when you start a conversation."
      aside={<SavedHint savedAt={savedAt} />}
      testId="settings-assistant"
    >
      {!available && <NotStoredYet />}
      <SettingRow
        label="Answer for"
        description="Who answers are written for by default. You can still switch in any conversation."
        control={
          <Segmented
            label="Answer for"
            value={preferences.audience}
            choices={AUDIENCES}
            onChange={(audience) => save({ audience })}
            disabled={!available}
            testId="pref-audience"
          />
        }
      />
      <SettingRow
        label="Open sources under each answer"
        description="Show the list of sources as soon as an answer finishes, instead of behind a button."
        control={
          <Switch
            checked={preferences.sources_open}
            onCheckedChange={(sources_open) => save({ sources_open })}
            disabled={!available}
            aria-label="Open sources under each answer"
            data-testid="pref-sources-open"
          />
        }
      />
      <SettingRow
        label="Evidence timeline"
        description="With the sources, show when each was published and whether it supports or opposes the answer."
        control={
          <Switch
            checked={preferences.show_timeline}
            onCheckedChange={(show_timeline) => save({ show_timeline })}
            disabled={!available}
            aria-label="Show the evidence timeline"
          />
        }
      />
    </SettingsCard>
  );
}

// -- voice ------------------------------------------------------------------------------------

const RATES: ReadonlyArray<Choice<string>> = [
  { value: "0.85", label: "Slower" },
  { value: "1", label: "Normal" },
  { value: "1.15", label: "Faster" },
  { value: "1.3", label: "Fastest" },
];

const SAMPLE = "This is how the assistant will read its answers to you.";

export function VoiceSection() {
  const token = useToken();
  const { preferences, available } = usePreferences();
  const { save, savedAt } = usePreferenceSaver();
  const voices = useQuery({
    queryKey: ["voice", "voices"],
    queryFn: () => api.voices(token),
    enabled: Boolean(token),
    staleTime: 60 * 60_000,
    retry: false,
  });
  const [playing, setPlaying] = useState<string | null>(null);
  const queue = useRef<SpeechQueue | null>(null);
  const [micId, setMicId] = useState<string | undefined>(undefined);
  useEffect(() => setMicId(readSavedMicId()), []);
  useEffect(() => () => queue.current?.stop(), []);

  const play = (voice: string) => {
    queue.current?.stop();
    if (playing === voice) {
      setPlaying(null);
      return;
    }
    const q = new SpeechQueue(token, { onIdle: () => setPlaying(null) }, { voice, rate: preferences.voice_rate });
    queue.current = q;
    q.enqueue(SAMPLE);
    setPlaying(voice);
  };

  const selectable = voices.data?.selectable ?? false;
  const rate = RATES.reduce((best, r) =>
    Math.abs(Number(r.value) - preferences.voice_rate) < Math.abs(Number(best.value) - preferences.voice_rate) ? r : best,
  ).value;

  return (
    <div className="flex flex-col gap-4">
      <SettingsCard
        title="Read-aloud voice"
        description="The voice that reads answers in a voice conversation and when you press Read aloud."
        aside={<SavedHint savedAt={savedAt} />}
        testId="settings-voice"
      >
        {!available && <NotStoredYet />}
        {voices.data && !selectable && (
          <p className="pb-3 text-xs text-muted-foreground" role="note">
            This server reads answers with its own voice, so the choice below applies once voice runs on the cloud
            speech service.
          </p>
        )}
        <div role="radiogroup" aria-label="Read-aloud voice" className="grid gap-2 pb-4 sm:grid-cols-2">
          {(voices.data?.voices ?? []).map((voice) => {
            const chosen = preferences.voice_name === voice.id;
            return (
              <div
                key={voice.id}
                className={cn(
                  "flex items-center gap-2 rounded-xl border p-2.5 transition-colors",
                  chosen ? "border-primary/60 bg-primary/5" : "hover:bg-muted/40",
                )}
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={chosen}
                  disabled={!available}
                  onClick={() => save({ voice_name: voice.id })}
                  className="flex min-w-0 flex-1 flex-col items-start text-left"
                  data-testid={`voice-${voice.id}`}
                >
                  <span className="text-sm font-medium">{voice.label}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {voice.gender === "female" ? "Female" : "Male"} · {voice.accent} · {voice.tone}
                  </span>
                </button>
                {selectable && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => play(voice.id)}
                    aria-label={playing === voice.id ? `Stop ${voice.label}` : `Play ${voice.label}`}
                  >
                    {playing === voice.id ? <Pause /> : <Play />}
                  </Button>
                )}
              </div>
            );
          })}
          {voices.isLoading && <p className="text-sm text-muted-foreground">Loading voices…</p>}
        </div>
        <SettingRow
          label="Speaking pace"
          description="Pitch stays natural at every pace."
          control={
            <Segmented
              label="Speaking pace"
              value={rate}
              choices={RATES}
              onChange={(value) => save({ voice_rate: Number(value) })}
              disabled={!available}
              testId="pref-voice-rate"
            />
          }
        />
        <SettingRow
          label="Keep listening after each answer"
          description="In a voice conversation, listen for your next question as soon as an answer is read. Off: tap Talk each time."
          control={
            <Switch
              checked={preferences.voice_continuous}
              onCheckedChange={(voice_continuous) => save({ voice_continuous })}
              disabled={!available}
              aria-label="Keep listening after each answer"
              data-testid="pref-voice-continuous"
            />
          }
        />
      </SettingsCard>

      <SettingsCard
        title="Microphone"
        description="Check that the browser can hear you, and choose which microphone voice uses on this device."
      >
        <MicCheck deviceId={micId} onDeviceChange={setMicId} />
      </SettingsCard>
    </div>
  );
}

// -- learn ---------------------------------------------------------------------------------------

const SCOPES: ReadonlyArray<Choice<Preferences["learn_scope"]>> = [
  { value: "all", label: "All" },
  { value: "mbbs", label: "MBBS" },
  { value: "pg", label: "PG" },
];

const DEPTHS: ReadonlyArray<Choice<Preferences["learn_depth"]>> = [
  { value: "auto", label: "Follow the subject", hint: "MBBS depth for MBBS subjects, PG for PG specialties" },
  { value: "mbbs", label: "Always MBBS" },
  { value: "pg", label: "Always PG" },
];

export function LearnSection() {
  const { preferences, available } = usePreferences();
  const { save, savedAt } = usePreferenceSaver();
  const specialties = useSpecialties();
  const followed = useMemo(() => new Set(preferences.followed_specialties), [preferences.followed_specialties]);

  const toggle = (slug: string) => {
    const next = followed.has(slug)
      ? preferences.followed_specialties.filter((s) => s !== slug)
      : [...preferences.followed_specialties, slug];
    save({ followed_specialties: next });
  };

  return (
    <SettingsCard
      title="What Learn shows"
      description="Which subjects the Learn tab lists, and how deep its explanations go."
      aside={<SavedHint savedAt={savedAt} />}
      testId="settings-learn"
    >
      {!available && <NotStoredYet />}
      <SettingRow
        label="Subjects to list"
        description="MBBS subjects, PG specialties, or both."
        control={
          <Segmented
            label="Subjects to list"
            value={preferences.learn_scope}
            choices={SCOPES}
            onChange={(learn_scope) => save({ learn_scope })}
            disabled={!available}
            testId="pref-learn-scope"
          />
        }
      />
      <SettingRow
        label="Teaching depth"
        description="Where a topic is taught at MBBS or PG level before you choose."
        control={
          <Segmented
            label="Teaching depth"
            value={preferences.learn_depth}
            choices={DEPTHS}
            onChange={(learn_depth) => save({ learn_depth })}
            disabled={!available}
          />
        }
      />
      <SettingRow label="Your subjects" description="Pinned to the top of Learn. Tap to add or remove.">
        <div className="mt-3 flex flex-wrap gap-1.5" data-testid="pref-followed">
          {(specialties.data ?? []).map((s) => {
            const on = followed.has(s.slug);
            return (
              <button
                key={s.slug}
                type="button"
                aria-pressed={on}
                disabled={!available}
                onClick={() => toggle(s.slug)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs transition-colors disabled:opacity-50",
                  on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
                data-testid={`follow-${s.slug}`}
              >
                {s.name}
              </button>
            );
          })}
        </div>
      </SettingRow>
    </SettingsCard>
  );
}

// -- notifications -----------------------------------------------------------------------------

export function NotificationsSection() {
  return (
    <div className="flex flex-col gap-4">
      <DigestPreferencesCard />
      <SettingsCard
        title="Answer updates"
        description="When new evidence changes an answer you follow (the bell on any answer), it appears in Notifications."
      >
        <SettingRow
          label="Notifications"
          description="Updates to followed answers and organisation events."
          control={
            <Link href="/app/notifications" className={buttonVariants({ variant: "outline" })}>
              Open notifications
            </Link>
          }
        />
      </SettingsCard>
    </div>
  );
}

// -- privacy & data ----------------------------------------------------------------------------

export function PrivacySection() {
  const token = useToken();
  const [exporting, setExporting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const download = async () => {
    setExporting(true);
    try {
      const { blob, filename } = await api.exportData(token);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't prepare the download.");
    } finally {
      setExporting(false);
    }
  };

  const remove = async () => {
    setDeleting(true);
    try {
      const out = await api.deleteConversations(token);
      setConfirming(false);
      toast.success(
        out.kept
          ? `Deleted ${out.deleted} conversation${out.deleted === 1 ? "" : "s"}. ${out.kept} kept: an answer in them is in a binder, shared, or has updates.`
          : `Deleted ${out.deleted} conversation${out.deleted === 1 ? "" : "s"}.`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't delete the conversations.");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <SettingsCard title="What ClinicalContext keeps" description="Plainly, and nothing else.">
        <ul className="flex flex-col gap-2 text-sm">
          {[
            "Your questions and their answers, so you can go back to them — Chat, Learn, Treatment and Evidence search.",
            "Feedback you give on answers, and the binders and notes you create.",
            "Never patient-identifying details: a message with a name, phone number or record number is refused before any model sees it.",
            "Nothing from the public chatbot or symptom check on the website.",
          ].map((line) => (
            <li key={line} className="flex gap-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-teal-600 dark:text-teal-300" aria-hidden />
              <span className="text-pretty">{line}</span>
            </li>
          ))}
        </ul>
      </SettingsCard>

      <SettingsCard title="Your data" testId="settings-privacy">
        <SettingRow
          label="Download a copy"
          description="Your profile, preferences, conversations with their sources, and feedback, as a JSON file."
          control={
            <Button variant="outline" onClick={() => void download()} disabled={exporting} data-testid="export-data">
              <Download /> {exporting ? "Preparing…" : "Download"}
            </Button>
          }
        />
        <SettingRow
          label="Delete my conversations"
          description="Deletes your Chat, Learn, Treatment and voice conversations for good. Evidence searches stay — they are your organisation's record."
          control={
            <Button variant="destructive" onClick={() => setConfirming(true)} data-testid="delete-conversations">
              <Trash2 /> Delete…
            </Button>
          }
        />
      </SettingsCard>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete all your conversations?</DialogTitle>
            <DialogDescription>
              Your Chat, Learn, Treatment and voice conversations, and their answers, are deleted and can&rsquo;t be
              recovered. A conversation stays if an answer in it is saved to a binder, shared, or followed for
              updates — remove it from there first.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void remove()} disabled={deleting} data-testid="confirm-delete">
              {deleting ? "Deleting…" : "Delete conversations"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
