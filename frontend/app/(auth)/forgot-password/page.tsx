"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type FormEvent } from "react";

import { OtpForm } from "@/components/auth/otp-form";
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
import { createClient } from "@/lib/supabase/client";
import { describeAuthError } from "@/lib/supabase/errors";

/**
 * Forgot password, step one: the email. Supabase sends a reset link (and a
 * code, if the template carries one) that lands on /auth/callback with
 * `next=/reset-password`, where step two sets the new password. The
 * confirmation deliberately does not say whether the address has an
 * account.
 */
function ForgotPasswordForm() {
  const supabase = useMemo(createClient, []);
  const searchParams = useSearchParams();
  const [email, setEmail] = useState(searchParams.get("email") ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function requestReset(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent("/reset-password")}`,
    });
    setPending(false);
    if (resetError) {
      setError(describeAuthError(resetError, "password-reset"));
      return;
    }
    setSent(true);
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Reset your password</CardTitle>
        <CardDescription>
          Enter the email you signed up with and we will send a link to choose a new password.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {sent ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm" role="status">
              If an account exists for <span className="font-medium">{email}</span>, a
              password-reset email is on its way. Follow the link to choose a new
              password. If it has not arrived in a few minutes, check the spam folder.
            </p>
            <OtpForm email={email} kind="recovery" next="/reset-password" />
          </div>
        ) : (
          <form onSubmit={requestReset} className="flex flex-col gap-4">
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
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={pending}>
              {pending ? "Sending…" : "Send reset link"}
            </Button>
          </form>
        )}
        <p className="text-sm text-muted-foreground">
          Remembered it?{" "}
          <Link className="underline underline-offset-4" href="/login">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function ForgotPasswordPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <ForgotPasswordForm />
      </Suspense>
    </main>
  );
}
