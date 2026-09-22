import { createServerClient } from "@supabase/ssr";

import { cookieOptions } from "@/lib/supabase/cookies";
import { supabaseUrl } from "@/lib/supabase/env";
import { NextResponse, type NextRequest } from "next/server";

const PROTECTED_PREFIXES = ["/app", "/onboarding", "/reset-password"];
const AUTH_PAGES = ["/login", "/signup"];

/**
 * Refreshes the Supabase session cookie and enforces route protection:
 * unauthenticated users cannot reach /app/* or /onboarding; authenticated
 * users are bounced from /login and /signup to /app.
 */
export async function updateSession(request: NextRequest, requestHeaders: Headers) {
  // The headers the root middleware set (the CSP nonce) must reach the page.
  let supabaseResponse = NextResponse.next({ request: { headers: requestHeaders } });

  const supabase = createServerClient(
    supabaseUrl(),
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookieOptions,
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          // request.cookies writes through to the request's Cookie header;
          // carry the refreshed value into the headers the page will see,
          // alongside the nonce the root middleware put there.
          const cookie = request.headers.get("cookie");
          if (cookie !== null) requestHeaders.set("cookie", cookie);
          supabaseResponse = NextResponse.next({ request: { headers: requestHeaders } });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // IMPORTANT: getUser() validates against Supabase. Never trust getSession()
  // alone in the middleware. Unreachable Supabase (e.g. unprovisioned local
  // env) degrades to "not authenticated" — protected routes stay protected.
  let isAuthenticated = false;
  try {
    const {
      data: { user },
    } = await supabase.auth.getUser();
    isAuthenticated = user !== null;
  } catch {
    isAuthenticated = false;
  }

  const path = request.nextUrl.pathname;
  const needsAuth = PROTECTED_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
  const isAuthPage = AUTH_PAGES.some(
    (page) => path === page || path.startsWith(`${page}/`),
  );

  if (needsAuth && !isAuthenticated) {
    const url = request.nextUrl.clone();
    url.search = "";
    if (path.startsWith("/reset-password")) {
      // Only a password-reset link can open this page; without a session
      // the link has expired or was used, and a fresh one is the fix.
      url.pathname = "/login";
      url.searchParams.set("error", "password_reset_expired");
      return NextResponse.redirect(url);
    }
    url.pathname = "/login";
    url.searchParams.set("next", path);
    return NextResponse.redirect(url);
  }

  if (isAuthPage && isAuthenticated) {
    const url = request.nextUrl.clone();
    url.pathname = "/app";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}
