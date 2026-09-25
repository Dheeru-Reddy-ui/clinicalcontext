/**
 * Typed access to every backend endpoint the UI uses.
 *
 * Response types come straight from the OpenAPI-generated schemas; the only
 * hand-written shapes are the ones the API carries as opaque JSON (see
 * `lib/domain.ts`). Every function takes the access token explicitly — no
 * ambient auth — so server components, client hooks, and tests call it alike.
 */

import { apiFetch, apiUrl, ApiError } from "@/lib/api";
import type { Schemas } from "@/lib/domain";

export type { ApiError };

export interface QueryDetail {
  query: {
    id: string;
    raw_query: string;
    contextualized_query: string | null;
    query_type: string | null;
    mode: "standard" | "comparison";
    pico: Schemas["PICO"] | null;
    status: string;
    created_at: string;
  };
  guardrail_verdict: {
    allowed?: boolean;
    blocked_by?: string | null;
    escalation?: boolean;
    findings?: Array<{ code?: string; message?: string }>;
  };
  answer: {
    id: string;
    content: string;
    citations: unknown;
    confidence: string | null;
    evidence_grade: string | null;
    has_contradiction: boolean;
    abstained: boolean;
    model: string;
    prompt_version: string;
    latency_ms: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
    cost_usd: number | null;
    cached: boolean;
    cache_saved_usd: number | null;
    comparison_table: unknown;
    reasoning: unknown;
    created_at: string;
  } | null;
  retrieval_traces: Array<{
    stage: string;
    chunk_ids: string[];
    scores: number[];
    duration_ms: number | null;
  }>;
}

export interface HistoryItem {
  id: string;
  raw_query: string;
  query_type: string | null;
  mode: "standard" | "comparison";
  status: string;
  created_at: string;
  confidence: string | null;
  abstained: boolean | null;
  has_contradiction: boolean | null;
  cost_usd: number | null;
  cached: boolean | null;
}

export interface HistoryPage {
  total: number;
  limit: number;
  offset: number;
  items: HistoryItem[];
}

export interface AnswerVersionsOut {
  answer_id: string;
  versions: Array<{
    version: number;
    content: string;
    citations: unknown;
    confidence: string | null;
    diff: unknown;
    superseded_by_document_ids: string[];
    created_at: string;
  }>;
  following: boolean;
  /** True when a Living Answer rerun produced a newer version than v1. */
  superseded: boolean;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // -- identity / org -------------------------------------------------------------
  me: (token: string) => apiFetch<Schemas["MeOut"]>("/api/v1/orgs/me", { accessToken: token }),
  members: (token: string) =>
    apiFetch<Schemas["MembersOut"]>("/api/v1/orgs/members", { accessToken: token }),
  invites: (token: string) =>
    apiFetch<Schemas["InvitesOut"]>("/api/v1/orgs/invites", { accessToken: token }),
  createInvite: (token: string, body: Schemas["InviteCreateRequest"]) =>
    apiFetch<Schemas["InviteOut"]>("/api/v1/orgs/invites", {
      method: "POST",
      body,
      accessToken: token,
    }),
  changeRole: (token: string, memberId: string, body: Schemas["RoleUpdateRequest"]) =>
    apiFetch<Schemas["MemberOut"]>(`/api/v1/orgs/members/${memberId}/role`, {
      method: "PATCH",
      body,
      accessToken: token,
    }),

  // -- queries --------------------------------------------------------------------
  queryDetail: (token: string, id: string) =>
    apiFetch<QueryDetail>(`/api/v1/queries/${id}`, { accessToken: token }),
  history: (
    token: string,
    params: {
      limit?: number;
      offset?: number;
      status?: string;
      has_contradiction?: boolean;
      abstained?: boolean;
      mine?: boolean;
    } = {},
  ) => apiFetch<HistoryPage>(`/api/v1/queries${qs(params)}`, { accessToken: token }),

  // -- sessions -------------------------------------------------------------------
  sessions: (token: string, params: { limit?: number; offset?: number; mine?: boolean } = {}) =>
    apiFetch<Schemas["SessionsOut"]>(`/api/v1/sessions${qs(params)}`, { accessToken: token }),
  session: (token: string, id: string) =>
    apiFetch<Schemas["SessionDetailOut"]>(`/api/v1/sessions/${id}`, { accessToken: token }),
  createSession: (token: string, body: Schemas["SessionCreateRequest"]) =>
    apiFetch<Schemas["SessionOut"]>("/api/v1/sessions", {
      method: "POST",
      body,
      accessToken: token,
    }),

  // -- feedback -------------------------------------------------------------------
  feedback: (token: string, body: Schemas["FeedbackRequest"]) =>
    apiFetch<Schemas["FeedbackOut"]>("/api/v1/feedback", {
      method: "POST",
      body,
      accessToken: token,
    }),

  // -- documents ------------------------------------------------------------------
  documents: (
    token: string,
    params: {
      limit?: number;
      offset?: number;
      scope?: "all" | "shared" | "private";
      search?: string;
      source_type?: string;
      study_type?: string;
      evidence_grade?: string;
    } = {},
  ) => apiFetch<Schemas["DocumentsOut"]>(`/api/v1/documents${qs(params)}`, { accessToken: token }),
  document: (token: string, id: string) =>
    apiFetch<Schemas["DocumentDetail"]>(`/api/v1/documents/${id}`, { accessToken: token }),
  documentChunks: (token: string, id: string, params: { limit?: number; offset?: number } = {}) =>
    apiFetch<Schemas["DocumentChunksOut"]>(`/api/v1/documents/${id}/chunks${qs(params)}`, {
      accessToken: token,
    }),
  uploadDocument: async (token: string, file: File, title?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    const response = await fetch(apiUrl("/api/v1/documents/upload"), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!response.ok) {
      const envelope = (await response.json().catch(() => ({}))) as {
        error?: { code?: string; message?: string; request_id?: string | null };
      };
      throw new ApiError(
        response.status,
        envelope.error?.code ?? "unknown_error",
        envelope.error?.message ?? response.statusText,
        envelope.error?.request_id ?? null,
      );
    }
    return (await response.json()) as Schemas["DocumentUploadOut"];
  },

  // -- analytics ------------------------------------------------------------------
  analyticsOverview: (token: string, days = 30) =>
    apiFetch<Schemas["UsageOverview"]>(`/api/v1/analytics/overview${qs({ days })}`, {
      accessToken: token,
    }),
  analyticsUsage: (token: string, days = 30) =>
    apiFetch<Schemas["UsageSeries"]>(`/api/v1/analytics/usage${qs({ days })}`, {
      accessToken: token,
    }),
  analyticsQuality: (token: string, days = 30) =>
    apiFetch<Schemas["QualityReport"]>(`/api/v1/analytics/quality${qs({ days })}`, {
      accessToken: token,
    }),
  analyticsCost: (token: string, days = 30) =>
    apiFetch<Schemas["CostReport"]>(`/api/v1/analytics/cost${qs({ days })}`, {
      accessToken: token,
    }),

  // -- suggest --------------------------------------------------------------------
  suggest: (token: string, q: string) =>
    apiFetch<Schemas["SuggestOut"]>(`/api/v1/suggest${qs({ q })}`, { accessToken: token }),

  // -- binders --------------------------------------------------------------------
  binders: (token: string) =>
    apiFetch<Schemas["BindersOut"]>("/api/v1/binders", { accessToken: token }),
  binder: (token: string, id: string) =>
    apiFetch<Schemas["BinderDetailOut"]>(`/api/v1/binders/${id}`, { accessToken: token }),
  createBinder: (token: string, body: Schemas["BinderCreateRequest"]) =>
    apiFetch<Schemas["BinderOut"]>("/api/v1/binders", { method: "POST", body, accessToken: token }),
  updateBinder: (token: string, id: string, body: Schemas["BinderUpdateRequest"]) =>
    apiFetch<Schemas["BinderOut"]>(`/api/v1/binders/${id}`, {
      method: "PATCH",
      body,
      accessToken: token,
    }),
  deleteBinder: (token: string, id: string) =>
    apiFetch<void>(`/api/v1/binders/${id}`, { method: "DELETE", accessToken: token }),
  addBinderItem: (token: string, id: string, body: Schemas["BinderItemCreateRequest"]) =>
    apiFetch<Schemas["BinderItemOut"]>(`/api/v1/binders/${id}/items`, {
      method: "POST",
      body,
      accessToken: token,
    }),
  removeBinderItem: (token: string, id: string, itemId: string) =>
    apiFetch<void>(`/api/v1/binders/${id}/items/${itemId}`, {
      method: "DELETE",
      accessToken: token,
    }),
  annotate: (token: string, id: string, itemId: string, body: Schemas["AnnotationCreateRequest"]) =>
    apiFetch<Schemas["AnnotationOut"]>(`/api/v1/binders/${id}/items/${itemId}/annotations`, {
      method: "POST",
      body,
      accessToken: token,
    }),

  // -- sharing --------------------------------------------------------------------
  shareLinks: (token: string) =>
    apiFetch<Schemas["ShareLinksOut"]>("/api/v1/sharing/links", { accessToken: token }),
  createShareLink: (token: string, answerId: string) =>
    apiFetch<Schemas["ShareLinkOut"]>("/api/v1/sharing/links", {
      method: "POST",
      body: { answer_id: answerId },
      accessToken: token,
    }),
  revokeShareLink: (token: string, id: string) =>
    apiFetch<void>(`/api/v1/sharing/links/${id}`, { method: "DELETE", accessToken: token }),
  sharingPolicy: (token: string) =>
    apiFetch<Schemas["SharingPolicyOut"]>("/api/v1/sharing/policy", { accessToken: token }),
  setSharingPolicy: (token: string, enabled: boolean) =>
    apiFetch<Schemas["SharingPolicyOut"]>("/api/v1/sharing/policy", {
      method: "PATCH",
      body: { public_sharing_enabled: enabled },
      accessToken: token,
    }),
  /** Unauthenticated: the public permalink surface. */
  publicAnswer: (slug: string) => apiFetch<Schemas["PublicAnswerOut"]>(`/api/public/answers/${slug}`),
  /** A committed eval result file, as its runner wrote it (methodology page). */
  publicEval: <T = Record<string, unknown>,>(
    name: "voice" | "adversarial" | "golden" | "ablation" | "calibration",
  ) => apiFetch<T>(`/api/public/evals/${name}`),

  // -- voice (Phase 11) ---------------------------------------------------------------
  voiceConfig: (token: string) =>
    apiFetch<Schemas["VoiceConfigOut"]>("/api/v1/voice/config", { accessToken: token }),
  voiceSettings: (token: string) =>
    apiFetch<Schemas["VoiceSettingsOut"]>("/api/v1/voice/settings", { accessToken: token }),
  setVoiceSettings: (token: string, quality: Schemas["VoiceSettingsIn"]["tts_quality"]) =>
    apiFetch<Schemas["VoiceSettingsOut"]>("/api/v1/voice/settings", {
      method: "PATCH",
      body: { tts_quality: quality },
      accessToken: token,
    }),
  voiceTurns: (
    token: string,
    params: { query_session_id?: string; voice_session_id?: string; limit?: number; offset?: number } = {},
  ) => apiFetch<Schemas["VoiceTurnList"]>(`/api/v1/voice/turns${qs(params)}`, { accessToken: token }),
  voiceAnalytics: (token: string, days = 30) =>
    apiFetch<Schemas["VoiceAnalyticsOut"]>(`/api/v1/voice/analytics${qs({ days })}`, {
      accessToken: token,
    }),

  // -- evaluation loop (Phase 12) -------------------------------------------------
  reviewQueue: (token: string, params: { status?: string; limit?: number } = {}) =>
    apiFetch<Schemas["ReviewQueueOut"]>(`/api/v1/feedback/review-queue${qs(params)}`, {
      accessToken: token,
    }),
  // -- living answers -------------------------------------------------------------
  follow: (token: string, answerId: string) =>
    apiFetch<{ answer_id: string; following: boolean }>(`/api/v1/answers/${answerId}/follow`, {
      method: "POST",
      accessToken: token,
    }),
  unfollow: (token: string, answerId: string) =>
    apiFetch<void>(`/api/v1/answers/${answerId}/follow`, { method: "DELETE", accessToken: token }),
  versions: (token: string, answerId: string) =>
    apiFetch<AnswerVersionsOut>(`/api/v1/answers/${answerId}/versions`, { accessToken: token }),

  // -- notifications --------------------------------------------------------------
  notifications: (token: string, unreadOnly = false) =>
    apiFetch<Schemas["NotificationsOut"]>(
      `/api/v1/notifications${qs({ unread_only: unreadOnly || undefined })}`,
      { accessToken: token },
    ),
  markRead: (token: string, id: string) =>
    apiFetch<Schemas["NotificationOut"]>(`/api/v1/notifications/${id}/read`, {
      method: "POST",
      accessToken: token,
    }),
  digestPreferences: (token: string) =>
    apiFetch<Schemas["DigestPreferencesOut"]>("/api/v1/digest/preferences", { accessToken: token }),
  updateDigestPreferences: (token: string, body: Schemas["DigestPreferencesUpdate"]) =>
    apiFetch<Schemas["DigestPreferencesOut"]>("/api/v1/digest/preferences", {
      method: "PATCH",
      body,
      accessToken: token,
    }),
  markAllRead: (token: string) =>
    apiFetch<void>("/api/v1/notifications/read-all", { method: "POST", accessToken: token }),

  // -- settings: the person's own ---------------------------------------------------
  preferences: (token: string) =>
    apiFetch<Schemas["PreferencesOut"]>("/api/v1/me/preferences", { accessToken: token }),
  updatePreferences: (token: string, body: Schemas["PreferencesUpdate"]) =>
    apiFetch<Schemas["PreferencesOut"]>("/api/v1/me/preferences", {
      method: "PATCH",
      body,
      accessToken: token,
    }),
  updateProfile: (token: string, body: Schemas["ProfileUpdate"]) =>
    apiFetch<Schemas["MeOut"]>("/api/v1/me/profile", { method: "PATCH", body, accessToken: token }),
  deleteConversations: (token: string) =>
    apiFetch<Schemas["ConversationsDeletedOut"]>("/api/v1/me/conversations", {
      method: "DELETE",
      accessToken: token,
    }),
  deleteConversation: (token: string, sessionId: string) =>
    apiFetch<Schemas["ConversationsDeletedOut"]>(`/api/v1/chat/sessions/${sessionId}`, {
      method: "DELETE",
      accessToken: token,
    }),
  voices: (token: string) =>
    apiFetch<Schemas["VoicesOut"]>("/api/v1/voice/voices", { accessToken: token }),
  /** A copy of the person's data as a JSON file, and the name the server gave it. */
  exportData: async (token: string): Promise<{ blob: Blob; filename: string }> => {
    const response = await fetch(apiUrl("/api/v1/me/export"), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const body = (await response.json()) as { error?: { message?: string } };
        message = body.error?.message ?? message;
      } catch {
        /* not JSON */
      }
      throw new ApiError(response.status, "export_failed", message, null);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "clinicalcontext-export.json";
    return { blob: await response.blob(), filename };
  },

  // -- api keys (admin) -----------------------------------------------------------
  apiKeys: (token: string) =>
    apiFetch<Schemas["ApiKeysOut"]>("/api/v1/api-keys", { accessToken: token }),
  createApiKey: (token: string, body: Schemas["ApiKeyCreateRequest"]) =>
    apiFetch<Schemas["ApiKeyCreatedOut"]>("/api/v1/api-keys", {
      method: "POST",
      body,
      accessToken: token,
    }),
  revokeApiKey: (token: string, id: string) =>
    apiFetch<void>(`/api/v1/api-keys/${id}`, { method: "DELETE", accessToken: token }),
};
