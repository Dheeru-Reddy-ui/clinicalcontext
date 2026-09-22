"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";

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
import { createAccount, warmApi } from "@/lib/signup";
import { createClient } from "@/lib/supabase/client";
import { describeAuthError } from "@/lib/supabase/errors";

const ALREADY_REGISTERED =
  "An account with this email already exists. Sign in instead, or reset your password if you have forgotten it.";

// A sign-up normally takes well under a second; past this the wait is the
// sleeping free-tier API starting up, and the form says so.
const SLOW_MS = 8_000;

function onboardingPathFor(inviteToken: string | null): string {
  return inviteToken ? `/onboarding?invite=${encodeURIComponent(inviteToken)}` : "/onboarding";
}

function SignupForm() {
  const supabase = useMemo(createClient, []);
  const router = useRouter();
  const searchParams = useSearchParams();
  // An invite token travels: signup → (email confirm) → onboarding.
  const inviteToken = searchParams.get("invite");

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [alreadyRegistered, setAlreadyRegistered] = useState(false);
  const [slow, setSlow] = useState(false);

  // The API sleeps after fifteen quiet minutes and takes up to two to start.
  // Waking it while the form is being filled in keeps that wait off the
  // submit button.
  useEffect(warmApi, []);

  async function signUp(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setAlreadyRegistered(false);
    setSlow(false);
    // Past this the wait is the free-tier instance starting, not the work.
    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);

    // Create the account through our API (no email involved), then sign in
    // with the same password so Supabase mints the session into this browser.
    const created = await createAccount({ email, password, fullName });
    clearTimeout(slowTimer);
    setSlow(false);
    if (!created.ok) {
      setPending(false);
      if (created.kind === "exists") {
        setAlreadyRegistered(true);
        return;
      }
      setError(created.message);
      return;
    }

    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
    setPending(false);
    if (signInError) {
      // The account exists now, so send them to sign in rather than leaving
      // them on a form that would report the address as taken.
      setError(
        `${describeAuthError(signInError, "sign-in")} Your account was created — try signing in.`,
      );
      return;
    }
    router.push(onboardingPathFor(inviteToken));
    router.refresh();
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Create your account</CardTitle>
        <CardDescription>
          {inviteToken
            ? "You've been invited to join an organization."
            : "Start a new organization for your team."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {alreadyRegistered ? (
          <div className="flex flex-col gap-3" role="alert" data-testid="already-registered">
            <p className="text-sm">{ALREADY_REGISTERED}</p>
            <div className="flex flex-wrap gap-3 text-sm">
              <Link
                className="underline underline-offset-4"
                href={`/login?email=${encodeURIComponent(email)}`}
              >
                Sign in
              </Link>
              <Link
                className="underline underline-offset-4"
                href={`/forgot-password?email=${encodeURIComponent(email)}`}
              >
                Reset password
              </Link>
              <button
                type="button"
                className="underline underline-offset-4"
                onClick={() => setAlreadyRegistered(false)}
              >
                Use a different email
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={signUp} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="fullName">Full name</Label>
              <Input
                id="fullName"
                autoComplete="name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {slow && (
              <p className="text-sm text-muted-foreground" role="status">
                Still working — the server sleeps after fifteen quiet minutes and takes up to
                two to start. Your details are safe; this finishes on its own.
              </p>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={pending}>
              {pending ? "Creating account…" : "Sign up"}
            </Button>
          </form>
        )}
        <p className="text-sm text-muted-foreground">
          Already registered?{" "}
          <Link className="underline underline-offset-4" href="/login">
            Log in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function SignupPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <SignupForm />
      </Suspense>
    </main>
  );
}
