"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/components/providers/auth-provider";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";

/** The current access token, or "" while the session is loading. */
export function useToken(): string {
  const { session } = useAuth();
  return session?.access_token ?? "";
}

export const keys = {
  history: (params: object) => ["history", params] as const,
  query: (id: string) => ["query", id] as const,
  sessions: (params: object) => ["sessions", params] as const,
  session: (id: string) => ["session", id] as const,
  documents: (params: object) => ["documents", params] as const,
  document: (id: string) => ["document", id] as const,
  chunks: (id: string) => ["chunks", id] as const,
  binders: ["binders"] as const,
  binder: (id: string) => ["binder", id] as const,
  notifications: ["notifications"] as const,
  digestPreferences: ["digest-preferences"] as const,
  overview: (days: number) => ["analytics", "overview", days] as const,
  usage: (days: number) => ["analytics", "usage", days] as const,
  quality: (days: number) => ["analytics", "quality", days] as const,
  cost: (days: number) => ["analytics", "cost", days] as const,
  members: ["members"] as const,
  invites: ["invites"] as const,
  apiKeys: ["api-keys"] as const,
  sharingPolicy: ["sharing-policy"] as const,
  shareLinks: ["share-links"] as const,
  versions: (id: string) => ["versions", id] as const,
  voiceConfig: ["voice", "config"] as const,
  voiceSettings: ["voice", "settings"] as const,
  voiceTurns: (sessionId: string | null) => ["voice-turns", sessionId] as const,
  voiceAnalytics: (days: number) => ["voice", "analytics", days] as const,
  reviewQueue: (status: string | undefined) => ["feedback", "review-queue", status ?? "all"] as const,
  publicEval: (name: string) => ["public", "evals", name] as const,
};

export function useHistory(params: Parameters<typeof api.history>[1] = {}) {
  const token = useToken();
  return useQuery({
    queryKey: keys.history(params),
    queryFn: () => api.history(token, params),
    enabled: Boolean(token),
  });
}

export function useQueryDetail(id: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.query(id ?? ""),
    queryFn: () => api.queryDetail(token, id ?? ""),
    enabled: Boolean(token && id),
  });
}

export function useSessions(params: Parameters<typeof api.sessions>[1] = {}) {
  const token = useToken();
  return useQuery({
    queryKey: keys.sessions(params),
    queryFn: () => api.sessions(token, params),
    enabled: Boolean(token),
  });
}

export function useSession(id: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.session(id ?? ""),
    queryFn: () => api.session(token, id ?? ""),
    enabled: Boolean(token && id),
  });
}

export function useDocuments(params: Parameters<typeof api.documents>[1] = {}, enabled = true) {
  const token = useToken();
  return useQuery({
    queryKey: keys.documents(params),
    queryFn: () => api.documents(token, params),
    enabled: Boolean(token) && enabled,
    placeholderData: (previous) => previous,
  });
}

export function useDocument(id: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.document(id ?? ""),
    queryFn: () => api.document(token, id ?? ""),
    enabled: Boolean(token && id),
  });
}

export function useDocumentChunks(id: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.chunks(id ?? ""),
    queryFn: () => api.documentChunks(token, id ?? "", { limit: 200 }),
    enabled: Boolean(token && id),
  });
}

export function useBinders() {
  const token = useToken();
  return useQuery({
    queryKey: keys.binders,
    queryFn: () => api.binders(token),
    enabled: Boolean(token),
  });
}

export function useBinder(id: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.binder(id ?? ""),
    queryFn: () => api.binder(token, id ?? ""),
    enabled: Boolean(token && id),
  });
}

export function useNotifications() {
  const token = useToken();
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.notifications(token),
    enabled: Boolean(token),
    refetchInterval: 60_000,
  });
}

export function useAnalytics(days: number) {
  const token = useToken();
  const overview = useQuery({
    queryKey: keys.overview(days),
    queryFn: () => api.analyticsOverview(token, days),
    enabled: Boolean(token),
  });
  const usage = useQuery({
    queryKey: keys.usage(days),
    queryFn: () => api.analyticsUsage(token, days),
    enabled: Boolean(token),
  });
  const quality = useQuery({
    queryKey: keys.quality(days),
    queryFn: () => api.analyticsQuality(token, days),
    enabled: Boolean(token),
  });
  const cost = useQuery({
    queryKey: keys.cost(days),
    queryFn: () => api.analyticsCost(token, days),
    enabled: Boolean(token),
  });
  return { overview, usage, quality, cost };
}

export function useVersions(answerId: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.versions(answerId ?? ""),
    queryFn: () => api.versions(token, answerId ?? ""),
    enabled: Boolean(token && answerId),
  });
}

/** Feedback: optimistic — the thumb lights up immediately, rolls back on failure. */
export function useFeedback() {
  const token = useToken();
  return useMutation({
    mutationFn: (body: Schemas["FeedbackRequest"]) => api.feedback(token, body),
  });
}

export function useCreateBinder() {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["BinderCreateRequest"]) => api.createBinder(token, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.binders }),
  });
}

export function useAddToBinder() {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ binderId, body }: { binderId: string; body: Schemas["BinderItemCreateRequest"] }) =>
      api.addBinderItem(token, binderId, body),
    onSuccess: (_data, { binderId }) => {
      void client.invalidateQueries({ queryKey: keys.binder(binderId) });
      void client.invalidateQueries({ queryKey: keys.binders });
    },
  });
}

export function useAnnotate(binderId: string) {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, body }: { itemId: string; body: Schemas["AnnotationCreateRequest"] }) =>
      api.annotate(token, binderId, itemId, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.binder(binderId) }),
  });
}

export function useDigestPreferences() {
  const token = useToken();
  return useQuery({
    queryKey: keys.digestPreferences,
    queryFn: () => api.digestPreferences(token),
    enabled: Boolean(token),
  });
}

export function useUpdateDigestPreferences() {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["DigestPreferencesUpdate"]) => api.updateDigestPreferences(token, body),
    onSuccess: (data) => client.setQueryData(keys.digestPreferences, data),
  });
}

export function useMarkRead() {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string | "all"): Promise<void> => {
      if (id === "all") await api.markAllRead(token);
      else await api.markRead(token, id);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useFollow(answerId: string) {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (follow: boolean): Promise<void> => {
      if (follow) await api.follow(token, answerId);
      else await api.unfollow(token, answerId);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.versions(answerId) }),
  });
}

export function useShare() {
  const token = useToken();
  return useMutation({ mutationFn: (answerId: string) => api.createShareLink(token, answerId) });
}


// -- voice (Phase 11) -------------------------------------------------------------------

export function useVoiceConfig() {
  const token = useToken();
  return useQuery({
    queryKey: keys.voiceConfig,
    queryFn: () => api.voiceConfig(token),
    enabled: Boolean(token),
    staleTime: 5 * 60_000,
  });
}

export function useVoiceSettings() {
  const token = useToken();
  return useQuery({
    queryKey: keys.voiceSettings,
    queryFn: () => api.voiceSettings(token),
    enabled: Boolean(token),
  });
}

export function useSetVoiceSettings() {
  const token = useToken();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (quality: Schemas["VoiceSettingsIn"]["tts_quality"]) =>
      api.setVoiceSettings(token, quality),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.voiceSettings });
      void client.invalidateQueries({ queryKey: keys.voiceConfig });
    },
  });
}

export function useVoiceTurns(querySessionId: string | null) {
  const token = useToken();
  return useQuery({
    queryKey: keys.voiceTurns(querySessionId),
    queryFn: () => api.voiceTurns(token, { query_session_id: querySessionId ?? undefined, limit: 100 }),
    enabled: Boolean(token && querySessionId),
  });
}

export function useVoiceAnalytics(days: number) {
  const token = useToken();
  return useQuery({
    queryKey: keys.voiceAnalytics(days),
    queryFn: () => api.voiceAnalytics(token, days),
    enabled: Boolean(token),
  });
}

export function useReviewQueue(status?: string, enabled = true) {
  const token = useToken();
  return useQuery({
    queryKey: keys.reviewQueue(status),
    queryFn: () => api.reviewQueue(token, { status }),
    enabled: Boolean(token) && enabled,
  });
}

export function usePublicEval<T>(name: "golden" | "ablation" | "calibration") {
  return useQuery({
    queryKey: keys.publicEval(name),
    queryFn: () => api.publicEval<T>(name),
    staleTime: 5 * 60_000,
  });
}
