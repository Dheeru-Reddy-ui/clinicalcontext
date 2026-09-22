import { NextResponse, type NextRequest } from "next/server";

import { contentSecurityPolicy, mintNonce, readCspEnvironment } from "@/lib/csp";
import { updateSession } from "@/lib/supabase/middleware";

const SESSION_ROUTES = ["/app", "/onboarding", "/login", "/signup", "/forgot-password", "/reset-password"];

/**
 * Two jobs on every page request. A per-request Content-Security-Policy
 * nonce: it goes on the *request* so Next.js stamps it onto every script it
 * emits, and the finished policy goes on the *response* for the browser
 * (lib/csp.ts). And on the routes that need it, the Supabase session
 * refresh and the sign-in redirects.
 */
export async function middleware(request: NextRequest) {
  const nonce = mintNonce();
  const policy = contentSecurityPolicy(readCspEnvironment(), nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", policy);

  const path = request.nextUrl.pathname;
  const needsSession = SESSION_ROUTES.some((route) => path === route || path.startsWith(`${route}/`));
  const response = needsSession
    ? await updateSession(request, requestHeaders)
    : NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("content-security-policy", policy);
  return response;
}

export const config = {
  // Every document; not the framework's static assets, the service worker,
  // or files with an extension (images, fonts, the manifest). Written with
  // character classes, not backslash escapes: Next runs the matcher through
  // path-to-regexp, which strips the escapes, and `.*.[a-z]+$` then excludes
  // every path ending in a letter — /login included. Found by the middleware
  // silently not running on the routes it exists for.
  matcher: ["/((?!_next/static|_next/image|favicon[.]ico|sw[.]js|.*[.][a-zA-Z0-9]+$).*)"],
};
