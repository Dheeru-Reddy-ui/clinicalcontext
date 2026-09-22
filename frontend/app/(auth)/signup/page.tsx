"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type FormEvent } from "react";

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
import { OtpForm } from "@/components/auth/otp-form";
import { createClient } from "@/lib/supabase/client";

const ALREADY_REGISTERED =
  "An account with this email already exists. Sign in instead, or reset your password if you have forgotten it.";

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
  const [confirmationSent, setConfirmationSent] = useState(false);

  async function signUp(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setAlreadyRegistered(false);

    const onboardingPath = onboardingPathFor(inviteToken);

    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { full_name: fullName },
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(onboardingPath)}`,
      },
    });
    setPending(false);
    if (signUpError) {
      // With email confirmation off, a duplicate is a plain error.
      if (/already registered/i.test(signUpError.message)) {
        setAlreadyRegistered(true);
        return;
      }
      setError(signUpError.message);
      return;
    }
    if (data.session) {
      // Email confirmation disabled → straight to onboarding.
      router.push(onboardingPath);
      router.refresh();
      return;
    }
    // With email confirmation on, Supabase answers a duplicate with a
    // success that carries a user with no identities, so that the response
    // alone does not reveal who has an account. The person typing their own
    // email deserves a straight answer, though.
    if (data.user && (data.user.identities?.length ?? 0) === 0) {
      setAlreadyRegistered(true);
      return;
    }
    setConfirmationSent(true);
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
        {confirmationSent ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm">
              Confirmation email sent to{" "}
              <span className="font-medium">{email}</span>. Follow the link to
              continue. If it has not arrived in a few minutes, check the spam
              folder.
            </p>
            <OtpForm email={email} kind="email" next={onboardingPathFor(inviteToken)} />
          </div>
        ) : alreadyRegistered ? (
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
