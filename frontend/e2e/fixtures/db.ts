import { Pool } from "pg";

import { env } from "./env";

/**
 * Direct database access for the assertions that must not go through the API.
 *
 * Several gates are only meaningfully proven at the row level — "the feedback
 * row was written", "org B cannot reach that chunk", "no second graph run
 * happened". Asserting those through the UI would be asserting the UI.
 */

let pool: Pool | undefined;

export function db(): Pool {
  if (!pool) pool = new Pool({ connectionString: env.databaseUrl, max: 4 });
  return pool;
}

export async function closeDb(): Promise<void> {
  await pool?.end();
  pool = undefined;
}

export async function rows<T extends Record<string, unknown> = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const result = await db().query(sql, params);
  return result.rows as T[];
}

export async function one<T extends Record<string, unknown> = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
): Promise<T | null> {
  const result = await rows<T>(sql, params);
  return result[0] ?? null;
}

export async function scalar<T = unknown>(sql: string, params: unknown[] = []): Promise<T> {
  const result = await db().query(sql, params);
  const row = result.rows[0] as Record<string, unknown> | undefined;
  return (row ? Object.values(row)[0] : undefined) as T;
}

/**
 * Run a statement as a tenant would: the `authenticated` role with the JWT
 * claims a Supabase request carries. This is the context RLS actually sees,
 * so a query that succeeds here is a query a tenant could really run.
 */
export async function asTenant<T extends Record<string, unknown> = Record<string, unknown>>(
  orgId: string,
  userId: string,
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const client = await db().connect();
  try {
    await client.query("BEGIN");
    await client.query("SET LOCAL ROLE authenticated");
    await client.query("SELECT set_config('request.jwt.claims', $1, true)", [
      JSON.stringify({ role: "authenticated", org_id: orgId, sub: userId }),
    ]);
    const result = await client.query(sql, params);
    await client.query("COMMIT");
    return result.rows as T[];
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
