import { apiUrl } from "@/lib/api";

/**
 * Sign-up goes through our API rather than straight to Supabase.
 *
 * Supabase's own sign-up sends a confirmation email, and its built-in mailer
 * refuses every address outside the project team — so on the deployment it
 * answered 500 and created nothing. The API creates the account with the
 * service-role key instead (backend app/services/signup.py), sends no email,
 * and the browser signs in straight afterwards with the same password.
 */

export type SignupOutcome =
  | { ok: true }
  | { ok: false; kind: "exists" }
  | { ok: false; kind: "message"; message: string };

const WAKING =
  "The server is starting up — this can take up to two minutes on the free tier. Try again in a moment.";

/** Wake a sleeping free-tier instance, so the form is not the thing that waits. */
export function warmApi(): void {
  void fetch(apiUrl("/ready"), { cache: "no-store" }).catch(() => {
    /* best effort: the sign-up itself reports any real problem */
  });
}

export async function createAccount(input: {
  email: string;
  password: string;
  fullName: string;
}): Promise<SignupOutcome> {
  let response: Response;
  try {
    response = await fetch(apiUrl("/api/public/signup"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: input.email,
        password: input.password,
        full_name: input.fullName,
      }),
    });
  } catch {
    return { ok: false, kind: "message", message: WAKING };
  }

  if (response.ok) return { ok: true };
  if (response.status === 409) return { ok: false, kind: "exists" };

  const body = (await response.json().catch(() => null)) as
    | { error?: { message?: string }; detail?: unknown }
    | null;
  const message = body?.error?.message;
  if (typeof message === "string" && message) {
    return { ok: false, kind: "message", message };
  }
  if (response.status === 422) {
    // FastAPI's own schema rejection — the form's own rules already cover
    // the cases a person can hit, so this is a fallback sentence.
    return { ok: false, kind: "message", message: "Check the email and password and try again." };
  }
  if (response.status === 503 || response.status === 502) {
    return { ok: false, kind: "message", message: WAKING };
  }
  if (response.status === 404) {
    // The frontend deploys on its own (Vercel git integration); the API is
    // deployed deliberately. A 404 here means this build is newer than the
    // API it is talking to, which is a deployment order problem and not
    // anything the person filling in the form can fix.
    return {
      ok: false,
      kind: "message",
      message:
        "Sign-up is not available on this server yet — it is running an older version than this page. Try again shortly.",
    };
  }
  return {
    ok: false,
    kind: "message",
    message: "The account could not be created. Try again in a moment.",
  };
}
