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
import { describeAuthError } from "@/lib/supabase/errors";

// What the auth callback says when a link did not work; shown here so a
// failed link is a sentence, not a silent return to the form.
const CALLBACK_MESSAGES: Record<string, string> = {
  auth_callback_failed:
    "That sign-in link did not work. It may have expired or already been used — request a new one below.",
  password_reset_expired:
    "That password-reset link has expired or was already used. Request a new one.",
  different_browser:
    "That link was opened in a different browser than the one that requested it. Open it where you asked for it, or request a new one here and enter the 6-digit code from the email instead.",
};

function LoginForm() {
  const supabase = useMemo(createClient, []);
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") ?? "/app";
  const callbackError = searchParams.get("error");
  const notice = searchParams.get("message");

  const [email, setEmail] = useState(searchParams.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(
    callbackError ? (CALLBACK_MESSAGES[callbackError] ?? callbackError) : null,
  );
  const [magicLinkSent, setMagicLinkSent] = useState(false);

  async function signInWithPassword(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    setPending(false);
    if (signInError) {
      setError(describeAuthError(signInError, "sign-in"));
      return;
    }
    router.push(next);
    router.refresh();
  }

  async function sendMagicLink() {
    if (!email) {
      setError("Enter your email first, then request a magic link.");
      return;
    }
    setPending(true);
    setError(null);
    const { error: otpError } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    setPending(false);
    if (otpError) {
      setError(describeAuthError(otpError, "magic-link"));
      return;
    }
    setMagicLinkSent(true);
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Log in</CardTitle>
        <CardDescription>
          ClinicalContext AI — evidence-grounded clinical answers.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {notice && (
          <p className="text-sm" role="status">
            {notice}
          </p>
        )}
        {magicLinkSent ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm">
              Magic link sent to <span className="font-medium">{email}</span>.
              Check your inbox — and the spam folder if it takes a few minutes.
            </p>
            <OtpForm email={email} kind="email" next={next} />
          </div>
        ) : (
          <form onSubmit={signInWithPassword} className="flex flex-col gap-4">
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
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={pending}>
              {pending ? "Signing in…" : "Sign in"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => void sendMagicLink()}
            >
              Email me a magic link
            </Button>
          </form>
        )}
        <p className="text-sm text-muted-foreground">
          <Link
            className="underline underline-offset-4"
            href={email ? `/forgot-password?email=${encodeURIComponent(email)}` : "/forgot-password"}
          >
            Forgot your password?
          </Link>
        </p>
        <p className="text-sm text-muted-foreground">
          No account?{" "}
          <Link className="underline underline-offset-4" href="/signup">
            Sign up
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
