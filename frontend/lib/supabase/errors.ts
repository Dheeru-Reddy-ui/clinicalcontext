import type { AuthError } from "@supabase/supabase-js";

/**
 * What an auth step was trying to do, so a server failure can be explained
 * in terms of it.
 */
export type AuthStep = "sign-up" | "sign-in" | "magic-link" | "password-reset" | "code" | "new-password";

const SERVER_FAILURE: Record<AuthStep, string> = {
  "sign-up":
    "The server could not send the confirmation email, so the account was not created. " +
    "That is an email-delivery problem on the server — its mail provider is not set up, or refused this address — not a problem with your details.",
  "magic-link":
    "The server could not send the magic link. Email delivery on the server is not set up, or it refused this address.",
  "password-reset":
    "The server could not send the reset email. Email delivery on the server is not set up, or it refused this address.",
  "sign-in": "The sign-in service had an internal error. Try again in a moment.",
  code: "The sign-in service had an internal error while checking the code. Try again in a moment.",
  "new-password": "The sign-in service had an internal error while saving the password. Try again in a moment.",
};

/**
 * A sentence for an auth error.
 *
 * Supabase's client treats every 5xx as a retryable network failure and
 * builds its message from the raw Response object rather than the JSON the
 * server sent — which stringifies to "{}". So the one time the server has
 * something important to say ("Error sending confirmation email"), the
 * user sees two braces. This says what a 5xx means for the step at hand,
 * and otherwise passes the server's own words through.
 */
export function describeAuthError(error: AuthError, step: AuthStep): string {
  const status = (error as { status?: number }).status;
  const message = error.message?.trim() ?? "";
  const opaque = message === "" || message === "{}";
  if ((typeof status === "number" && status >= 500) || opaque) {
    return SERVER_FAILURE[step];
  }
  return message;
}
