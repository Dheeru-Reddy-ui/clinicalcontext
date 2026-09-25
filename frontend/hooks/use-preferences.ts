"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useOptionalAuth } from "@/components/providers/auth-provider";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";

/** Every preference present: the server's values over the defaults. */
export type Preferences = Required<Schemas["PreferencesOut"]>;
export type PreferencesChange = Schemas["PreferencesUpdate"];

/** What the product does before a person has chosen anything. */
export const DEFAULT_PREFERENCES: Preferences = {
  audience: "patient",
  sources_open: false,
  show_timeline: true,
  voice_name: "thalia",
  voice_rate: 1,
  voice_continuous: true,
  learn_scope: "all",
  learn_depth: "auto",
  followed_specialties: [],
  saved: false,
  updated_at: null,
};

export const PREFERENCES_KEY = ["me", "preferences"] as const;

/**
 * The signed-in person's preferences (Settings), shared by every screen that
 * honours them — the chat's default audience, how answers open, the
 * read-aloud voice and pace, Learn's order.
 *
 * Never blocks a screen: signed out, still loading, or talking to a server
 * that predates Settings (404), the defaults apply.
 */
export function usePreferences() {
  const token = useOptionalAuth()?.session?.access_token ?? "";
  const query = useQuery({
    queryKey: PREFERENCES_KEY,
    queryFn: () => api.preferences(token),
    enabled: Boolean(token),
    staleTime: 5 * 60_000,
    retry: (failures, error) =>
      !(error instanceof ApiError && (error.status === 404 || error.status === 401)) && failures < 2,
  });
  return {
    preferences: query.data ? { ...DEFAULT_PREFERENCES, ...query.data } : DEFAULT_PREFERENCES,
    /** True once this person's saved preferences (or the server's defaults) have arrived. */
    loaded: query.isSuccess,
    /** False when the server cannot store preferences (not signed in, or not deployed yet). */
    available: Boolean(token) && !query.isError,
    query,
  };
}

/** Change some preferences; the screen updates at once and rolls back on failure. */
export function useUpdatePreferences() {
  const token = useOptionalAuth()?.session?.access_token ?? "";
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: PreferencesChange) => api.updatePreferences(token, change),
    onMutate: async (change) => {
      await client.cancelQueries({ queryKey: PREFERENCES_KEY });
      const previous = client.getQueryData<Schemas["PreferencesOut"]>(PREFERENCES_KEY);
      const next: Preferences = { ...DEFAULT_PREFERENCES, ...previous };
      for (const [key, value] of Object.entries(change)) {
        if (value !== null && value !== undefined) (next as Record<string, unknown>)[key] = value;
      }
      client.setQueryData(PREFERENCES_KEY, next);
      return { previous };
    },
    onError: (_error, _change, context) => {
      if (context?.previous) client.setQueryData(PREFERENCES_KEY, context.previous);
    },
    onSuccess: (saved) => client.setQueryData(PREFERENCES_KEY, saved),
  });
}
