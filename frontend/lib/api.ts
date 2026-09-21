/**
 * Backend API access. The base URL comes from NEXT_PUBLIC_API_URL
 * (see .env.example); all fetches to the backend go through apiFetch().
 */

import { noteResponse, traceHeaders } from "@/lib/telemetry";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

/** Mirrors backend app/schemas/health.py */
export type CheckStatus = "ok" | "error";

export interface ReadinessCheck {
  status: CheckStatus;
  detail: string | null;
  latency_ms: number | null;
}

export interface ReadinessResponse {
  status: "ok" | "degraded";
  checks: Record<string, ReadinessCheck>;
}

/** Mirrors backend app/schemas/tenancy.py */
export type OrgRole = "owner" | "clinician" | "viewer";
export type OrgPlan = "free" | "pro" | "enterprise";

export interface OrgOut {
  id: string;
  name: string;
  slug: string;
  plan: OrgPlan;
  created_at: string;
}

export interface MeOut {
  user_id: string;
  email: string | null;
  full_name: string | null;
  specialty: string | null;
  role: OrgRole;
  org: OrgOut;
}

export interface MemberOut {
  user_id: string;
  email: string | null;
  full_name: string | null;
  specialty: string | null;
  role: OrgRole;
  joined_at: string;
}

export interface InviteOut {
  id: string;
  email: string;
  role: OrgRole;
  token: string | null;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
}

/** Backend error envelope: {"error": {code, message, request_id}} */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string | null };
  detail?: unknown; // FastAPI validation errors
}

export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown; accessToken?: string } = {},
): Promise<T> {
  const { method = "GET", body, accessToken } = options;
  const headers: Record<string, string> = { ...traceHeaders() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const response = await fetch(apiUrl(path), {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  noteResponse(response);

  if (!response.ok) {
    let envelope: ErrorEnvelope = {};
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // non-JSON error body; fall through to generic message
    }
    throw new ApiError(
      response.status,
      envelope.error?.code ?? "unknown_error",
      envelope.error?.message ??
        (envelope.detail ? JSON.stringify(envelope.detail) : response.statusText),
      envelope.error?.request_id ?? null,
    );
  }
  // 204 No Content (revokes, deletes, read-all) has no body to parse.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
