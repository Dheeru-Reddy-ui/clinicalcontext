"""Forward-only migration runner.

Applies the numbered SQL files in backend/migrations/ in order, exactly once
each, recording (version, sha256) in public.schema_migrations. A checksum
mismatch on an already-applied file aborts the run: applied migrations are
immutable — fix forward with a new numbered file.

Usage:
    uv run python -m scripts.migrate                 # apply to settings DATABASE_URL
    uv run python -m scripts.migrate --local-shim    # local compose / CI only
    uv run python -m scripts.migrate --dry-run
    uv run python -m scripts.migrate --url postgresql://...

--local-shim first applies migrations/local_shim.sql (Supabase auth-schema
parity for plain Postgres). The runner refuses the shim if the target looks
like a real Supabase instance. For Supabase, run WITHOUT the flag and use the
direct/session-mode connection string (port 5432, not the transaction pooler).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
LOCAL_SHIM_FILE = MIGRATIONS_DIR / "local_shim.sql"
_NUMBERED = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be applied safely."""


@dataclass
class MigrationReport:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)  # populated on dry runs
    shim_applied: bool = False


def discover_migrations() -> list[Path]:
    """The numbered migration files, in application order."""
    if not MIGRATIONS_DIR.is_dir():
        raise MigrationError(f"migrations directory not found: {MIGRATIONS_DIR}")
    return sorted(p for p in MIGRATIONS_DIR.iterdir() if _NUMBERED.match(p.name))


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


async def _looks_like_supabase(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'supabase_auth_admin'")
    return row is not None


async def _ensure_tracking_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version    text PRIMARY KEY,
            checksum   text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Infra table: clients never see it (no policies are ever added).
    await conn.execute("ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY")


async def apply_migrations(
    url: str,
    *,
    include_local_shim: bool = False,
    dry_run: bool = False,
) -> MigrationReport:
    """Apply all pending migrations. Idempotent; safe to run on every deploy."""
    report = MigrationReport()
    conn = await asyncpg.connect(url)
    try:
        # Deterministic name resolution on both local compose and Supabase
        # (where dashboard-enabled extensions live in the extensions schema).
        await conn.execute("SET search_path = public, extensions")

        if include_local_shim:
            if await _looks_like_supabase(conn):
                raise MigrationError(
                    "--local-shim refused: target looks like a real Supabase instance "
                    "(supabase_auth_admin role present). The shim is for plain Postgres only."
                )
            if not dry_run:
                async with conn.transaction():
                    await conn.execute(LOCAL_SHIM_FILE.read_text(encoding="utf-8"))
            report.shim_applied = True

        await _ensure_tracking_table(conn)
        rows = await conn.fetch("SELECT version, checksum FROM public.schema_migrations")
        already_applied = {str(r["version"]): str(r["checksum"]) for r in rows}

        for path in discover_migrations():
            sql = path.read_text(encoding="utf-8")
            digest = _checksum(sql)
            if path.name in already_applied:
                if already_applied[path.name] != digest:
                    raise MigrationError(
                        f"{path.name} changed after being applied "
                        f"(checksum drift). Applied migrations are immutable — "
                        f"write a new numbered migration instead."
                    )
                report.skipped.append(path.name)
                continue
            if dry_run:
                report.pending.append(path.name)
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO public.schema_migrations (version, checksum) VALUES ($1, $2)",
                    path.name,
                    digest,
                )
            report.applied.append(path.name)
    finally:
        await conn.close()
    return report


def _resolve_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    # Imported lazily so the module is usable (and testable) without env vars.
    from app.config import get_settings

    return get_settings().database_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Postgres DSN (default: settings DATABASE_URL)")
    parser.add_argument(
        "--local-shim",
        action="store_true",
        help="first apply the Supabase-parity auth shim (plain Postgres only)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args(argv)

    try:
        report = asyncio.run(
            apply_migrations(
                _resolve_url(args.url),
                include_local_shim=args.local_shim,
                dry_run=args.dry_run,
            )
        )
    except (MigrationError, asyncpg.PostgresError, OSError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1

    if report.shim_applied:
        print("local auth shim: applied" if not args.dry_run else "local auth shim: would apply")
    for name in report.skipped:
        print(f"skipped (already applied): {name}")
    for name in report.pending:
        print(f"pending: {name}")
    for name in report.applied:
        print(f"applied: {name}")
    if not report.applied and not report.pending:
        print("database is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
