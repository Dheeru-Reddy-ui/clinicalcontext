"use client";

import type { Session, User } from "@supabase/supabase-js";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiFetch, type MeOut, type OrgOut, type OrgRole } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

interface AuthState {
  loading: boolean;
  session: Session | null;
  user: User | null;
  /** Backend membership — null until bootstrap completes. */
  me: MeOut | null;
  org: OrgOut | null;
  role: OrgRole | null;
  /** Authenticated with Supabase but no org yet → onboarding required. */
  needsOnboarding: boolean;
  refreshMe: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const supabase = useMemo(createClient, []);
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<MeOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsOnboarding, setNeedsOnboarding] = useState(false);

  const loadMe = useCallback(async (activeSession: Session | null) => {
    if (!activeSession) {
      setMe(null);
      setNeedsOnboarding(false);
      return;
    }
    try {
      const payload = await apiFetch<MeOut>("/api/v1/orgs/me", {
        accessToken: activeSession.access_token,
      });
      setMe(payload);
      setNeedsOnboarding(false);
    } catch (error) {
      setMe(null);
      setNeedsOnboarding(
        error instanceof ApiError && error.code === "org_membership_required",
      );
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    supabase.auth
      .getSession()
      .then(async ({ data }) => {
        if (cancelled) return;
        setSession(data.session);
        await loadMe(data.session);
      })
      .catch(() => {
        // Unreachable Supabase (unprovisioned env) → treated as signed out.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      void loadMe(nextSession);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [supabase, loadMe]);

  const refreshMe = useCallback(async () => {
    const { data } = await supabase.auth.getSession();
    setSession(data.session);
    await loadMe(data.session);
  }, [supabase, loadMe]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    setMe(null);
    setNeedsOnboarding(false);
    router.push("/login");
  }, [supabase, router]);

  const value: AuthState = {
    loading,
    session,
    user: session?.user ?? null,
    me,
    org: me?.org ?? null,
    role: me?.role ?? null,
    needsOnboarding,
    refreshMe,
    signOut,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** The auth state where there is one — the website's public chatbot has none. */
export function useOptionalAuth(): AuthState | null {
  return useContext(AuthContext);
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return context;
}
