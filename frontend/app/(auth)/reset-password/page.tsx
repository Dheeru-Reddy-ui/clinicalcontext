"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { FormAlert } from "@/components/auth/form-alert";
import { PasswordField } from "@/components/auth/password-field";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { describeAuthError } from "@/lib/supabase/errors";

/**
 * Forgot password, step two. Reachable only with the session a reset link
 * (or code) established — the middleware sends anyone else to /login with
 * an explanation — so the only thing left to ask for is the new password.
 * That session is a real one, so a successful change goes straight into
 * the app rather than back to a login form the middleware would skip.
 */
export default function ResetPasswordPage() {
  const supabase = useMemo(createClient, []);
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setNewPassword(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);
    const { error: updateError } = await supabase.auth.updateUser({ password });
    setPending(false);
    if (updateError) {
      setError(describeAuthError(updateError, "new-password"));
      return;
    }
    toast.success("Your password has been changed.");
    router.push("/app");
    router.refresh();
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Choose a new password</CardTitle>
        <CardDescription>At least 8 characters.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={setNewPassword} className="flex flex-col gap-4">
          <PasswordField
            label="New password"
            autoComplete="new-password"
            minLength={8}
            hint="At least 8 characters."
            autoFocus
            value={password}
            onChange={setPassword}
          />
          <PasswordField
            label="Confirm new password"
            autoComplete="new-password"
            minLength={8}
            value={confirm}
            onChange={setConfirm}
          />
          {error && <FormAlert kind="error">{error}</FormAlert>}
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : "Save new password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
