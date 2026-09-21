"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { MembersPanel } from "@/components/admin/members-panel";
import { ErrorState, PageBody, PageHeader } from "@/components/clinical/page";
import { useAuth } from "@/components/providers/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { keys, useAnalytics, useSetVoiceSettings, useToken, useVoiceSettings } from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { formatDate, formatUsd } from "@/lib/text";

const PLAN_LIMITS: Record<string, { tenant: number; user: number; api_key: number }> = {
  free: { tenant: 30, user: 20, api_key: 60 },
  pro: { tenant: 300, user: 120, api_key: 600 },
  enterprise: { tenant: 2000, user: 600, api_key: 3000 },
};

export default function AdminPage() {
  const { role } = useAuth();
  if (role !== "owner") {
    return (
      <PageBody>
        <ErrorState error={Object.assign(new Error("Admin is available to organisation owners."), { status: 403 })} />
      </PageBody>
    );
  }
  return (
    <PageBody>
      <PageHeader title="Admin" description="Members, private corpus, sharing, API access, and plan." />
      <Tabs defaultValue="members">
        <TabsList aria-label="Admin sections">
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="uploads">Private corpus</TabsTrigger>
          <TabsTrigger value="access">Sharing &amp; API</TabsTrigger>
          <TabsTrigger value="plan">Plan &amp; usage</TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="pt-2">
          <MembersPanel />
        </TabsContent>
        <TabsContent value="uploads" className="pt-2">
          <Uploads />
        </TabsContent>
        <TabsContent value="access" className="space-y-4 pt-2">
          <SharingPolicy />
          <VoiceQuality />
          <ApiKeys />
        </TabsContent>
        <TabsContent value="plan" className="pt-2">
          <PlanUsage />
        </TabsContent>
      </Tabs>
    </PageBody>
  );
}

function Uploads() {
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
    <section className="max-w-xl space-y-3 rounded-lg border bg-card p-4">
      <div>
        <h2 className="text-sm font-medium">Upload a PDF to your private corpus</h2>
        <p className="text-xs text-muted-foreground">
          Parsed, classified, chunked and indexed for this organisation only. Never visible to other tenants. Max 25 MB.
        </p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="upload-file">PDF</Label>
        <Input id="upload-file" type="file" accept="application/pdf,.pdf" ref={fileRef} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="upload-title">Title (optional)</Label>
        <Input id="upload-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Defaults to the file name" />
      </div>
      <Button onClick={() => void upload()} disabled={busy}>
        <Upload /> {busy ? "Ingesting…" : "Upload"}
      </Button>
      {result && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border bg-muted/40 p-3 text-xs">
          <dt className="text-muted-foreground">Document</dt>
          <dd className="truncate">{result.title}</dd>
          <dt className="text-muted-foreground">Passages</dt>
          <dd>{result.chunk_count} ({result.embedded_count} indexed)</dd>
          <dt className="text-muted-foreground">Status</dt>
          <dd>{result.deduplicated ? "Deduplicated — identical content already existed" : "Ingested"}</dd>
        </dl>
      )}
    </section>
  );
}

/**
 * Phase 11F.1: ElevenLabs Flash (lowest time-to-first-byte) versus
 * Multilingual v2 (richer prosody, slower). A per-org trade-off, owner-only;
 * the waterfall on the dashboard shows what the choice costs.
 */
function VoiceQuality() {
  const settings = useVoiceSettings();
  const update = useSetVoiceSettings();
  const quality = settings.data?.tts_quality;
  return (
    <section className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-medium">Voice quality</h2>
          <p className="text-xs text-muted-foreground">
            <strong>Flash</strong> is the latency setting (≈75 ms model time-to-first-byte); <strong>Multilingual</strong> buys
            prosody and accent range at roughly three times the time-to-first-byte. Applies to the cloud backend; the
            offline backend uses the operating system voice either way.
          </p>
        </div>
        {settings.isPending ? (
          <Skeleton className="h-8 w-40" />
        ) : (
          <div className="flex shrink-0 rounded-md border p-0.5" role="radiogroup" aria-label="Voice quality">
            {(["flash", "multilingual"] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={quality === option}
                disabled={update.isPending}
                onClick={() =>
                  update.mutate(option, {
                    onSuccess: () => toast.success(option === "flash" ? "Voice set to Flash (latency)." : "Voice set to Multilingual (quality)."),
                    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not update."),
                  })
                }
                className={
                  "rounded px-3 py-1 text-xs font-medium capitalize " +
                  (quality === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent")
                }
              >
                {option}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function SharingPolicy() {
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
    <section className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-medium">Public answer links</h2>
          <p className="text-xs text-muted-foreground">
            Members can share read-only answer pages at <code>/a/&lt;slug&gt;</code>. Switching this off makes every existing link a clean 404 without deleting anything.
          </p>
        </div>
        {policy.isPending ? (
          <Skeleton className="h-6 w-10" />
        ) : (
          <Switch checked={policy.data?.public_sharing_enabled ?? false} onCheckedChange={(v) => void toggle(v)} disabled={saving} aria-label="Public sharing enabled" />
        )}
      </div>
      {links.data && links.data.links.length > 0 && (
        <ul className="divide-y rounded-md border text-xs">
          {links.data.links.map((l) => (
            <li key={l.id} className="flex items-center gap-3 px-3 py-2">
              <code className="font-mono">{l.path}</code>
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
      )}
    </section>
  );
}

function ApiKeys() {
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
    <section className="space-y-3 rounded-lg border bg-card p-4">
      <div>
        <h2 className="text-sm font-medium">API keys</h2>
        <p className="text-xs text-muted-foreground">Server-to-server access. Keys are shown once, rate-limited separately, and every request is audited.</p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="key-name">Name</Label>
          <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. EHR integration" className="w-56" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="key-scope">Scope</Label>
          <select id="key-scope" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)} className="h-8 rounded-md border bg-background px-2 text-sm">
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
        <div className="rounded-md border border-conflict/40 bg-conflict-bg/40 p-3 text-xs">
          <p className="font-medium text-conflict-fg">Copy this key now — it will not be shown again.</p>
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 break-all rounded-sm bg-background px-2 py-1 font-mono">{created.key}</code>
            <Button size="xs" variant="outline" onClick={() => void navigator.clipboard.writeText(created.key).then(() => toast.success("Copied"))}>
              <Copy /> Copy
            </Button>
          </div>
        </div>
      )}
      {list.data && list.data.api_keys.length > 0 && (
        <ul className="divide-y rounded-md border text-xs">
          {list.data.api_keys.map((k) => (
            <li key={k.id} className="flex items-center gap-3 px-3 py-2">
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
    </section>
  );
}

function PlanUsage() {
  const { org } = useAuth();
  const { overview } = useAnalytics(30);
  const plan = org?.plan ?? "free";
  const limits = PLAN_LIMITS[plan] ?? PLAN_LIMITS.free;
  return (
    <section className="grid gap-4 md:grid-cols-2">
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-sm font-medium">Plan</h2>
        <p className="mt-1 font-mono text-2xl font-semibold capitalize">{plan}</p>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Org limit</dt>
          <dd>{limits?.tenant} queries / min</dd>
          <dt className="text-muted-foreground">Per user</dt>
          <dd>{limits?.user} queries / min</dd>
          <dt className="text-muted-foreground">Per API key</dt>
          <dd>{limits?.api_key} queries / min</dd>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">Plan changes are handled by ClinicalContext; limits apply immediately.</p>
      </div>
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-sm font-medium">Last 30 days</h2>
        {overview.data ? (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">Queries</dt>
            <dd>{overview.data.total_queries.toLocaleString()}</dd>
            <dt className="text-muted-foreground">Spend</dt>
            <dd>{formatUsd(overview.data.total_cost_usd)}</dd>
            <dt className="text-muted-foreground">Saved by cache</dt>
            <dd>{formatUsd(overview.data.cost_saved_usd)}</dd>
            <dt className="text-muted-foreground">Blocked by guardrails</dt>
            <dd>{overview.data.blocked}</dd>
          </dl>
        ) : (
          <Skeleton className="mt-2 h-20 w-full" />
        )}
      </div>
    </section>
  );
}
