"use client";

import type { ReactNode } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import type { OrgRole } from "@/lib/api";

/**
 * Renders children only when the current user's role is in `roles`.
 * UI-level convenience only — the backend enforces RBAC on every route;
 * hiding a button never substitutes for a 403.
 */
export function RequireRole({
  roles,
  children,
  fallback = null,
}: {
  roles: OrgRole[];
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { role } = useAuth();
  if (!role || !roles.includes(role)) return <>{fallback}</>;
  return <>{children}</>;
}
