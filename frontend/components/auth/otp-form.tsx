"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { describeAuthError } from "@/lib/supabase/errors";

type OtpKind = "email" | "recovery";

/**
 * The alternative to clicking a link: the 6-digit code Supabase can put in
 * the same email (`{{ .Token }}` in the template). Links break when a mail
 * client rewrites them, when the redirect allow-list is wrong, or when the
 * link is opened on a different device; a code typed here always lands.
 *
 * `kind` is what the code proves — "email" for a sign-up confirmation or a
 * magic link, "recovery" for a password reset — and `next` is where to go
 * once it has.
 */
export function OtpForm({ email, kind, next }: { email: string; kind: OtpKind; next: string }) {
  const supabase = useMemo(createClient, []);
  const router = useRouter();
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function verify(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const { error: verifyError } = await supabase.auth.verifyOtp({
      email,
      token: code.trim(),
      type: kind,
    });
    setPending(false);
    if (verifyError) {
      setError(describeAuthError(verifyError, "code"));
      return;
    }
    router.push(next);
    router.refresh();
  }

  return (
    <form onSubmit={verify} className="flex flex-col gap-3 border-t pt-4">
      <div className="flex flex-col gap-2">
        <Label htmlFor="otp-code">Or enter the 6-digit code from the email</Label>
        <Input
          id="otp-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          placeholder="123456"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" variant="outline" disabled={pending || code.trim().length !== 6}>
        {pending ? "Checking…" : "Verify code"}
      </Button>
    </form>
  );
}
