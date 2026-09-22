import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

const OTP_TYPES: ReadonlySet<string> = new Set(["signup", "invite", "magiclink", "recovery", "email_change", "email"]);

/**
 * Where every emailed link lands: sign-up confirmation, magic link, invite,
 * password reset. Two link shapes exist — a PKCE `code`, or a `token_hash`
 * with its `type` — and both are exchanged for a session cookie here, then
 * forwarded to `next` (default /onboarding, which itself forwards
 * bootstrapped users to /app; a password reset asks for /reset-password).
 *
 * A link that fails is explained on the login page rather than swallowed:
 * Supabase sends `error_description` for an expired or reused link.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type");
  const next = safeNext(searchParams.get("next"));

  const supabase = await createClient();
  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${origin}${next}`);
    return failed(origin, next, error.message);
  }
  if (tokenHash && type && OTP_TYPES.has(type)) {
    const { error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type: type as EmailOtpType });
    if (!error) return NextResponse.redirect(`${origin}${next}`);
    return failed(origin, next, error.message);
  }
  const described = searchParams.get("error_description");
  return failed(origin, next, described ?? undefined);
}

/** Only a same-site path may be a redirect target. */
function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/onboarding";
  return value;
}

function failed(origin: string, next: string, reason?: string) {
  const url = new URL("/login", origin);
  let code = reason ?? (next.startsWith("/reset-password") ? "password_reset_expired" : "auth_callback_failed");
  // A PKCE link only works in the browser that requested it — the verifier
  // is in that browser's cookies. Opening the email on a phone after asking
  // on a laptop is the usual way to hit this, and the code in the email is
  // the way through.
  if (/code verifier/i.test(code)) code = "different_browser";
  url.searchParams.set("error", code);
  return NextResponse.redirect(url);
}
