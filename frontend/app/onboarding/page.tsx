"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch, type MeOut } from "@/lib/api";

function OnboardingContent() {
  const { session, me, loading, refreshMe } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [orgName, setOrgName] = useState("");
  const [inviteToken, setInviteToken] = useState(searchParams.get("invite") ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already bootstrapped → straight to the app.
  useEffect(() => {
    if (!loading && me) router.replace("/app");
  }, [loading, me, router]);

  async function bootstrap(body: { org_name?: string; invite_token?: string }) {
    if (!session) return;
    setPending(true);
    setError(null);
    try {
      await apiFetch<MeOut>("/api/v1/auth/bootstrap", {
        method: "POST",
        body,
        accessToken: session.access_token,
      });
      await refreshMe();
      router.replace("/app");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  }

  function createOrg(event: FormEvent) {
    event.preventDefault();
    void bootstrap({ org_name: orgName });
  }

  function joinOrg(event: FormEvent) {
    event.preventDefault();
    void bootstrap({ invite_token: inviteToken });
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="flex w-full max-w-md flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Set up your workspace
        </h1>
        <p className="text-sm text-muted-foreground">
          Create a new organization, or join one with an invite token.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Create an organization</CardTitle>
          <CardDescription>You&apos;ll be its owner.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={createOrg} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="orgName">Organization name</Label>
              <Input
                id="orgName"
                placeholder="e.g. Riverside Cardiology"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={pending}>
              {pending ? "Working…" : "Create organization"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Join with an invite</CardTitle>
          <CardDescription>
            Paste the invite token you received from an organization owner.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={joinOrg} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="inviteToken">Invite token</Label>
              <Input
                id="inviteToken"
                value={inviteToken}
                onChange={(e) => setInviteToken(e.target.value)}
                required
              />
            </div>
            <Button type="submit" variant="outline" disabled={pending}>
              {pending ? "Working…" : "Join organization"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <OnboardingContent />
      </Suspense>
    </main>
  );
}
