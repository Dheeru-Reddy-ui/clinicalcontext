/**
 * Cookie flags for the session (Phase 14.4).
 *
 * `secure` only in production: a Secure cookie is not sent over plain HTTP,
 * which would break local development. `sameSite: "lax"` lets the magic-link
 * redirect back from Supabase carry the session while still refusing
 * cross-site POSTs. `httpOnly` is deliberately absent — the browser Supabase
 * client has to read this cookie to refresh the session; the token is a
 * short-lived JWT and the real boundary is RLS, which trusts the token's
 * signature rather than the cookie.
 */
export const cookieOptions = {
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax",
  path: "/",
} as const;
