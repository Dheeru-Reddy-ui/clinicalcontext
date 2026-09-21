"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { API_BASE_URL, apiUrl, type ReadinessResponse } from "@/lib/api";

type ProbeState =
  | { phase: "loading" }
  | {
      phase: "ready";
      httpStatus: number;
      requestId: string | null;
      body: ReadinessResponse;
    }
  | { phase: "unreachable"; message: string };

function StatusBadge({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <Badge variant={ok ? "secondary" : "destructive"}>
      {label ?? (ok ? "ok" : "error")}
    </Badge>
  );
}

export default function HealthPage() {
  const [state, setState] = useState<ProbeState>({ phase: "loading" });

  const probe = useCallback(async () => {
    setState({ phase: "loading" });
    try {
      const response = await fetch(apiUrl("/ready"), { cache: "no-store" });
      const body = (await response.json()) as ReadinessResponse;
      setState({
        phase: "ready",
        httpStatus: response.status,
        requestId: response.headers.get("x-request-id"),
        body,
      });
    } catch (error) {
      setState({
        phase: "unreachable",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System health</h1>
        <p className="text-sm text-muted-foreground">
          Live readiness of the ClinicalContext backend at{" "}
          <code className="rounded bg-muted px-1 py-0.5">{API_BASE_URL}</code>
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Backend</span>
            {state.phase === "loading" && <Badge variant="outline">probing…</Badge>}
            {state.phase === "unreachable" && <StatusBadge ok={false} label="unreachable" />}
            {state.phase === "ready" && (
              <StatusBadge ok={state.body.status === "ok"} label={state.body.status} />
            )}
          </CardTitle>
          <CardDescription>
            {state.phase === "ready" &&
              `GET /ready → HTTP ${state.httpStatus}` +
                (state.requestId ? ` · request ${state.requestId}` : "")}
            {state.phase === "unreachable" &&
              `Could not reach the backend: ${state.message}. Is uvicorn running on port 8000?`}
            {state.phase === "loading" && "Contacting backend…"}
          </CardDescription>
        </CardHeader>

        {state.phase === "ready" && (
          <CardContent className="flex flex-col gap-3">
            {Object.entries(state.body.checks).map(([name, check]) => (
              <div
                key={name}
                className="flex items-center justify-between rounded-lg border p-3"
              >
                <div className="flex flex-col">
                  <span className="font-medium capitalize">{name}</span>
                  {check.detail && (
                    <span className="text-xs text-muted-foreground">{check.detail}</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {check.latency_ms !== null && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {check.latency_ms} ms
                    </span>
                  )}
                  <StatusBadge ok={check.status === "ok"} />
                </div>
              </div>
            ))}
          </CardContent>
        )}
      </Card>

      <div>
        <Button onClick={() => void probe()} disabled={state.phase === "loading"}>
          Refresh
        </Button>
      </div>
    </main>
  );
}
