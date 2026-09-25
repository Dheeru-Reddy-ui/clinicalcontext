"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Copy, KeyRound, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { MembersPanel } from "@/components/admin/members-panel";
import { useAuth } from "@/components/providers/auth-provider";
import { SettingRow, SettingsCard } from "@/components/settings/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  keys,
  useAnalytics,
  useSetVoiceSettings,
  useToken,
  useVoiceConfig,
  useVoiceSettings,
} from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { formatDate, formatUsd } from "@/lib/text";

/**
 * Workspace settings: what every member can see about their organisation,
 * and — for owners — members, public sharing, API keys, the private library
 * and the plan. (These were the Admin page; they live here now.)
 */

const PLAN_LIMITS: Record<string, { tenant: number; user: number; api_key: number }> = {
  free: { tenant: 30, user: 20, api_key: 60 },
  pro: { tenant: 300, user: 120, api_key: 600 },
  enterprise: { tenant: 2000, user: 600, api_key: 3000 },
};

const OWNER_PAGES = [
  { section: "members", label: "Members & invites", body: "Invite colleagues and set who can do what." },
  { section: "sharing", label: "Public sharing", body: "Whether answers can be shared as public links." },
  { section: "api", label: "API keys", body: "Server-to-server access for your own systems." },
  { section: "library", label: "Private library", body: "Your organisation's own PDFs, searched with the literature." },
  { section: "plan", label: "Plan & usage", body: "Limits, questions asked, spend." },
] as const;

export function OrganisationSection() {
  const { org, role } = useAuth();
  const token = useToken();
  const members = useQuery({
    queryKey: keys.members,
    queryFn: () => api.members(token),
    enabled: Boolean(token),
  });
  return (
    <div className="flex flex-col gap-4">
      <SettingsCard title="Workspace" description="The organisation your questions, answers and binders belong to." testId="settings-organisation">
        <SettingRow label="Name" control={<span className="text-sm font-medium">{org?.name}</span>} />
        <SettingRow label="Plan" control={<Badge variant="secondary" className="capitalize">{org?.plan}</Badge>} />
        <SettingRow
          label="Members"
          control={<span className="text-sm">{members.data ? members.data.members.length : "…"}</span>}
        />
        <SettingRow label="Your role" control={<Badge variant="outline" className="capitalize">{role}</Badge>} />
      </SettingsCard>
      {role === "owner" ? (
        <SettingsCard title="Manage" description="Owner settings for the whole organisation.">
          {OWNER_PAGES.map((page) => (
            <Link
              key={page.section}
              href={`/app/settings?section=${page.section}`}
              className="group flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
            >
              <span>
                <span className="block text-sm font-medium group-hover:text-primary">{page.label}</span>
                <span className="block text-xs text-muted-foreground">{page.body}</span>
              </span>
              <ArrowRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            </Link>
          ))}
        </SettingsCard>
      ) : (
        <p className="rounded-2xl border border-dashed p-4 text-sm text-muted-foreground">
          Members, sharing, API keys and the private library are managed by your organisation&rsquo;s owners.
        </p>
      )}
    </div>
  );
}

export function MembersSection() {
  return <MembersPanel embedded />;
}

// -- sharing ---------------------------------------------------------------------------------

export function SharingSection() {
  const token = useToken();
  const client = useQueryClient();
  const policy = useQuery({ queryKey: keys.sharingPolicy, queryFn: () => api.sharingPolicy(token), enabled: Boolean(token) });
  const links = useQuery({ queryKey: keys.shareLinks, queryFn: () => api.shareLinks(token), enabled: Boolean(token) });
  const [saving, setSaving] = useState(false);

  const toggle = async (enabled: boolean) => {
    setSaving(true);
    try {
      await api.setSharingPolicy(token, enabled);
      await client.invalidateQueries({ queryKey: keys.sharingPolicy });
      toast.success(enabled ? "Public sharing enabled." : "Public sharing disabled — every public link now returns 404.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update.");
    } finally {
      setSaving(false);
    }
  };

  const revoke = async (id: string) => {
    try {
      await api.revokeShareLink(token, id);
      await client.invalidateQueries({ queryKey: keys.shareLinks });
      toast.success("Link revoked.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not revoke.");
    }
  };

  return (
    <SettingsCard
      title="Public answer links"
      description={
        <>
          Members can share read-only answer pages at <code>/a/&lt;slug&gt;</code>. Switching this off makes every
          existing link a clean 404 without deleting anything.
        </>
      }
      aside={
        policy.isPending ? (
          <Skeleton className="h-6 w-10" />
        ) : (
          <Switch
            checked={policy.data?.public_sharing_enabled ?? false}
            onCheckedChange={(v) => void toggle(v)}
            disabled={saving}
            aria-label="Public sharing enabled"
          />
        )
      }
    >
      {links.data && links.data.links.length > 0 ? (
        <ul className="divide-y rounded-xl border text-xs">
          {links.data.links.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <code className="min-w-0 truncate font-mono">{l.path}</code>
              <Badge variant={l.enabled ? "secondary" : "outline"}>{l.enabled ? "live" : "revoked"}</Badge>
              <span className="ml-auto text-muted-foreground">{formatDate(l.created_at)}</span>
              {l.enabled && (
                <Button variant="ghost" size="xs" onClick={() => void revoke(l.id)} aria-label={`Revoke ${l.path}`}>
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No answers have been shared yet.</p>
      )}
    </SettingsCard>
  );
}

// -- API keys -------------------------------------------------------------------------------

export function ApiKeysSection() {
  const token = useToken();
  const client = useQueryClient();
  const list = useQuery({ queryKey: keys.apiKeys, queryFn: () => api.apiKeys(token), enabled: Boolean(token) });
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "query" | "full">("query");
  const [created, setCreated] = useState<Schemas["ApiKeyCreatedOut"] | null>(null);

  const create = async () => {
    try {
      const key = await api.createApiKey(token, { name: name.trim(), scopes: [scope] });
      setCreated(key);
      setName("");
      await client.invalidateQueries({ queryKey: keys.apiKeys });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the key.");
    }
  };
  const revoke = async (id: string) => {
    try {
      await api.revokeApiKey(token, id);
      await client.invalidateQueries({ queryKey: keys.apiKeys });
      toast.success("Key revoked.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not revoke.");
    }
  };

  return (
    <SettingsCard
      title="Keys for your systems"
      description="Server-to-server access. Keys are shown once, rate-limited separately, and every request is audited."
    >
      <div className="flex flex-wrap items-end gap-2 pb-4">
        <div className="min-w-0 flex-1 space-y-1 sm:flex-none">
          <Label htmlFor="key-name">Name</Label>
          <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. EHR integration" className="sm:w-56" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="key-scope">Scope</Label>
          <select
            id="key-scope"
            value={scope}
            onChange={(e) => setScope(e.target.value as typeof scope)}
            className="h-8 rounded-md border bg-background px-2 text-sm"
          >
            <option value="read">read</option>
            <option value="query">query</option>
            <option value="full">full</option>
          </select>
        </div>
        <Button size="sm" disabled={!name.trim()} onClick={() => void create()}>
          <KeyRound /> Create key
        </Button>
      </div>
      {created && (
        <div className="rounded-xl border border-conflict/40 bg-conflict-bg/40 p-3 text-xs">
          <p className="font-medium text-conflict-fg">Copy this key now — it will not be shown again.</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-sm bg-background px-2 py-1 font-mono">{created.key}</code>
            <Button size="xs" variant="outline" onClick={() => void navigator.clipboard.writeText(created.key).then(() => toast.success("Copied"))}>
              <Copy /> Copy
            </Button>
          </div>
        </div>
      )}
      {list.data && list.data.api_keys.length > 0 && (
        <ul className="mt-3 divide-y rounded-xl border text-xs">
          {list.data.api_keys.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <span className="font-medium">{k.name}</span>
              <code className="font-mono text-muted-foreground">{k.key_prefix}…</code>
              <Badge variant="outline">{k.scopes.join(", ")}</Badge>
              <span className="ml-auto text-muted-foreground">
                {k.revoked_at ? `revoked ${formatDate(k.revoked_at)}` : k.last_used_at ? `used ${formatDate(k.last_used_at)}` : "never used"}
              </span>
              {!k.revoked_at && (
                <Button variant="ghost" size="xs" onClick={() => void revoke(k.id)} aria-label={`Revoke ${k.name}`}>
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </SettingsCard>
  );
}

// -- private library ---------------------------------------------------------------------------

export function PrivateLibrarySection() {
  const token = useToken();
  const client = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Schemas["DocumentUploadOut"] | null>(null);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      const out = await api.uploadDocument(token, file, title.trim() || undefined);
      setResult(out);
      await client.invalidateQueries({ queryKey: ["documents"] });
      toast.success(out.deduplicated ? "Already in the corpus — linked the existing document." : `Ingested ${out.chunk_count} passages.`);
      if (fileRef.current) fileRef.current.value = "";
      setTitle("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsCard
      title="Add a document"
      description="Upload PDFs — protocols, guidelines — for your organisation only. Parsed, classified and searched alongside the literature; never visible to other organisations. Max 25 MB."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="upload-file">PDF</Label>
          <Input id="upload-file" type="file" accept="application/pdf,.pdf" ref={fileRef} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="upload-title">Title (optional)</Label>
          <Input id="upload-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Defaults to the file name" />
        </div>
        <div className="sm:col-span-2">
          <Button onClick={() => void upload()} disabled={busy}>
            <Upload /> {busy ? "Ingesting…" : "Upload"}
          </Button>
        </div>
      </div>
      {result && (
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-xl border bg-muted/40 p-3 text-xs">
          <dt className="text-muted-foreground">Document</dt>
          <dd className="truncate">{result.title}</dd>
          <dt className="text-muted-foreground">Passages</dt>
          <dd>
            {result.chunk_count} ({result.embedded_count} indexed)
          </dd>
          <dt className="text-muted-foreground">Status</dt>
          <dd>{result.deduplicated ? "Deduplicated — identical content already existed" : "Ingested"}</dd>
        </dl>
      )}
    </SettingsCard>
  );
}

// -- plan & usage ------------------------------------------------------------------------------

export function PlanSection() {
  const { org } = useAuth();
  const { overview } = useAnalytics(30);
  const plan = org?.plan ?? "free";
  const limits = PLAN_LIMITS[plan] ?? PLAN_LIMITS.free;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <SettingsCard title="Plan">
          <p className="font-mono text-2xl font-semibold capitalize">{plan}</p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">Organisation</dt>
            <dd>{limits?.tenant} questions / min</dd>
            <dt className="text-muted-foreground">Per person</dt>
            <dd>{limits?.user} questions / min</dd>
            <dt className="text-muted-foreground">Per API key</dt>
            <dd>{limits?.api_key} questions / min</dd>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">Plan changes are handled by ClinicalContext; limits apply immediately.</p>
        </SettingsCard>
        <SettingsCard title="Last 30 days">
          {overview.data ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Questions</dt>
              <dd>{overview.data.total_queries.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Spend</dt>
              <dd>{formatUsd(overview.data.total_cost_usd)}</dd>
              <dt className="text-muted-foreground">Saved by cache</dt>
              <dd>{formatUsd(overview.data.cost_saved_usd)}</dd>
              <dt className="text-muted-foreground">Blocked by guardrails</dt>
              <dd>{overview.data.blocked}</dd>
            </dl>
          ) : (
            <Skeleton className="h-20 w-full" />
          )}
        </SettingsCard>
      </div>
      <VoiceQualityCard />
    </div>
  );
}

/**
 * ElevenLabs Flash (latency) versus Multilingual (prosody): an owner-level
 * choice that only means something when voice speaks through ElevenLabs, so
 * it is only shown then.
 */
function VoiceQualityCard() {
  const config = useVoiceConfig();
  const settings = useVoiceSettings();
  const update = useSetVoiceSettings();
  if (config.data?.tts_provider !== "elevenlabs") return null;
  const quality = settings.data?.tts_quality;
  return (
    <SettingsCard
      title="Voice quality"
      description="Flash is the low-latency ElevenLabs model; Multilingual trades about three times the delay for richer prosody."
    >
      <div className="flex rounded-md border p-0.5" role="radiogroup" aria-label="Voice quality">
        {(["flash", "multilingual"] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={quality === option}
            disabled={update.isPending}
            onClick={() =>
              update.mutate(option, {
                onError: (e) => toast.error(e instanceof Error ? e.message : "Could not update."),
              })
            }
            className={
              "flex-1 rounded px-3 py-1 text-xs font-medium capitalize " +
              (quality === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent")
            }
          >
            {option}
          </button>
        ))}
      </div>
    </SettingsCard>
  );
}
