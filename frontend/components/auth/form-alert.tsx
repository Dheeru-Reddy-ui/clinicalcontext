import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";

/**
 * How every auth screen reports something.
 *
 * One component so a failed sign-in, a sent reset email and a duplicate
 * account all look like the same product. `error` uses role="alert" so a
 * screen reader interrupts with it; the quieter kinds use role="status",
 * which waits for a pause.
 */
export function FormAlert({
  kind,
  children,
}: {
  kind: "error" | "success" | "info";
  children: ReactNode;
}) {
  const Icon = kind === "error" ? AlertCircle : kind === "success" ? CheckCircle2 : Info;
  return (
    <Alert
      variant={kind === "error" ? "destructive" : "default"}
      role={kind === "error" ? "alert" : "status"}
    >
      <Icon aria-hidden />
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
