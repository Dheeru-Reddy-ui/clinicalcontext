/**
 * The Supabase project URL, normalised.
 *
 * The dashboard shows the REST endpoint (`…supabase.co/rest/v1`) far more
 * prominently than the bare project URL, and a copied trailing slash is
 * easy too. Either one breaks sign-in quietly: the auth client builds
 * `…/rest/v1/auth/v1/…`, and a path-restricted `connect-src` in the CSP
 * blocks the real auth calls. Strip both so the value only has to be right
 * to the host.
 */
export function supabaseUrl(raw: string | undefined = process.env.NEXT_PUBLIC_SUPABASE_URL): string {
  return (raw ?? "").replace(/\/+$/, "").replace(/\/(rest|auth|storage|realtime|functions)\/v1$/, "");
}
