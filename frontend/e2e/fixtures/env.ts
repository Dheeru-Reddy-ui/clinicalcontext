import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * The E2E suite talks to the same services the app does, so it reads the same
 * env files rather than a second copy of the truth. Nothing here is a secret
 * beyond what is already in the developer's working tree, and nothing is
 * written back.
 */

function loadEnvFile(path: string): Record<string, string> {
  const out: Record<string, string> = {};
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return out;
  }
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

const repoRoot = resolve(process.cwd(), "..");
const backendEnv = { ...loadEnvFile(resolve(repoRoot, ".env")), ...loadEnvFile(resolve(repoRoot, "backend/.env")) };
const frontendEnv = loadEnvFile(resolve(process.cwd(), ".env.local"));

function need(name: string, ...sources: Record<string, string | undefined>[]): string {
  for (const source of sources) {
    const value = source[name];
    if (value) return value;
  }
  throw new Error(`E2E: ${name} is not set (looked in process.env, .env, frontend/.env.local)`);
}

export const env = {
  baseUrl: process.env.E2E_BASE_URL ?? "http://localhost:3005",
  apiUrl: process.env.E2E_API_URL ?? frontendEnv.NEXT_PUBLIC_API_URL ?? "http://localhost:8010",
  databaseUrl: need("DATABASE_URL", process.env, backendEnv),
  supabaseUrl: need("NEXT_PUBLIC_SUPABASE_URL", process.env, frontendEnv),
  supabaseAnonKey: need("NEXT_PUBLIC_SUPABASE_ANON_KEY", process.env, frontendEnv),
  supabaseServiceKey: need("SUPABASE_SERVICE_ROLE_KEY", process.env, backendEnv),
  // Supabase's local mailpit; the digest and magic links land here.
  mailUrl: process.env.E2E_MAIL_URL ?? "http://127.0.0.1:54324",
  jaegerUrl: process.env.E2E_JAEGER_URL ?? "http://localhost:16686",
  otelEnabled: Boolean(backendEnv.OTEL_EXPORTER_OTLP_ENDPOINT),
};
