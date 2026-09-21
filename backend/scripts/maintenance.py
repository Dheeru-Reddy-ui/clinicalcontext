"""Database maintenance and a backup/restore drill (Phase 14.4).

    uv run python -m scripts.maintenance analyze     # VACUUM ANALYZE + index report
    uv run python -m scripts.maintenance backup      # pg_dump to a file
    uv run python -m scripts.maintenance restore-drill  # dump → fresh db → verify

The restore drill is the point of the backup: it dumps the live database,
restores it into a throwaway database, checks the row counts and the RLS
policy count match, and drops it again. A backup nobody has restored is a
hope, not a backup.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

from app.config import get_settings

BACKUPS = Path(__file__).resolve().parents[1] / ".backups"
# Tables whose row counts are compared between the live database and the
# restored copy; between them they cover the corpus, the tenants, and the
# answers a tenant would miss most.
COMPARED = (
    "documents",
    "chunks",
    "chunk_embeddings",
    "organizations",
    "profiles",
    "queries",
    "answers",
    "cost_events",
)


def _dsn_parts(dsn: str) -> tuple[str, str, str, str, str]:
    parsed = urlparse(dsn)
    return (
        parsed.hostname or "localhost",
        str(parsed.port or 5432),
        parsed.username or "postgres",
        parsed.password or "",
        (parsed.path or "/postgres").lstrip("/"),
    )


def _with_database(dsn: str, database: str) -> str:
    """The same DSN pointing at another database.

    Naive string replacement is a trap here: in
    `postgresql://clinicalcontext:clinicalcontext@host/clinicalcontext` the
    database name also appears as the username and the password.
    """
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


def _pg(tool: str, dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a Postgres client tool against a DSN, password via the environment.

    `pg_dump` and `pg_restore` are often not installed next to the
    application — they ship with the server. When the tool is missing but the
    compose database container is running, the same command runs inside it
    (`PGDUMP_CONTAINER` overrides the name); the dump then travels on stdout,
    which is also what a managed Postgres would give you.
    """
    import os
    import shutil

    host, port, user, password, database = _dsn_parts(dsn)
    env = {**os.environ, "PGPASSWORD": password}
    if shutil.which(tool):
        return subprocess.run(
            [tool, "-h", host, "-p", port, "-U", user, "-d", database, *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    container = os.environ.get("PGDUMP_CONTAINER", "clinicalcontext-postgres")
    return subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={password}",
            container,
            tool,
            "-h",
            "localhost",
            "-p",
            "5432",
            "-U",
            user,
            "-d",
            database,
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


# -- analyze ----------------------------------------------------------------------


async def analyze() -> int:
    """VACUUM ANALYZE, then report what the planner now knows."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        print("VACUUM ANALYZE...", flush=True)
        await conn.execute("VACUUM ANALYZE")
        rows = await conn.fetch(
            """
            SELECT relname,
                   n_live_tup,
                   n_dead_tup,
                   last_analyze,
                   last_autoanalyze
            FROM pg_stat_user_tables
            WHERE n_live_tup > 0
            ORDER BY n_live_tup DESC
            LIMIT 12
            """
        )
        print(f"{'table':24s} {'live':>9s} {'dead':>7s}  last analyze")
        for row in rows:
            when = row["last_analyze"] or row["last_autoanalyze"]
            print(
                f"{row['relname'][:24]:24s} {row['n_live_tup']:9d} {row['n_dead_tup']:7d}  "
                f"{when.strftime('%Y-%m-%d %H:%M') if when else 'never'}"
            )

        vectors = await conn.fetchval("SELECT count(*) FROM public.chunk_embeddings")
        index = await conn.fetchrow(
            """
            SELECT indexdef, pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
            FROM pg_indexes WHERE indexname = 'chunk_embeddings_hnsw'
            """
        )
        print()
        print(f"vectors: {vectors}")
        if index is not None:
            print(f"hnsw index: {index['size']}")
            print(f"  {index['indexdef']}")
            print(f"  hnsw.ef_search (per connection): {settings.hnsw_ef_search}")
            # m=16 suits corpora up to ~1M vectors; ef_construction trades
            # build time for recall and is worth raising when the corpus
            # outgrows a single ingest.
            if vectors > 500_000:
                print("  NOTE: past ~500k vectors, rebuild with m=24, ef_construction=128")
    finally:
        await conn.close()
    return 0


# -- backup / restore -------------------------------------------------------------


def backup(path: Path | None = None) -> Path:
    settings = get_settings()
    BACKUPS.mkdir(parents=True, exist_ok=True)
    target = path or BACKUPS / f"clinicalcontext-{datetime.now(UTC):%Y%m%dT%H%M%S}.dump"
    print(f"pg_dump to {target}", flush=True)
    import os
    import shutil

    if shutil.which("pg_dump"):
        done = _pg(
            "pg_dump",
            settings.database_url,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={target}",
        )
        if done.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {done.stderr.strip()[:400]}")
    else:
        _host, _port, user, password, database = _dsn_parts(settings.database_url)
        container = os.environ.get("PGDUMP_CONTAINER", "clinicalcontext-postgres")
        with target.open("wb") as handle:
            done_bytes = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    f"PGPASSWORD={password}",
                    container,
                    "pg_dump",
                    "-h",
                    "localhost",
                    "-p",
                    "5432",
                    "-U",
                    user,
                    "-d",
                    database,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                ],
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if done_bytes.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {done_bytes.stderr.decode()[:400]}")
    size = target.stat().st_size
    print(f"wrote {size / 1_048_576:.1f} MB")
    return target


async def restore_drill(keep: bool = False) -> int:
    """Dump, restore into a throwaway database, and compare what came back.

    `pg_dump` reads a consistent snapshot, so the restored copy matches the
    database *as it was when the dump began* — not as it is now. On a live
    system those differ, so the check is that every table's restored count
    falls between the count before the dump and the count after it. Comparing
    against "now" would fail the drill for the crime of the system being in
    use.
    """
    settings = get_settings()
    _host, _port, user, _password, database = _dsn_parts(settings.database_url)
    scratch = f"{database}_restore_drill"

    live = await asyncpg.connect(settings.database_url)
    before = {
        table: await live.fetchval(f"SELECT count(*) FROM public.{table}") for table in COMPARED
    }
    policies_before = await live.fetchval(
        "SELECT count(*) FROM pg_policies WHERE schemaname='public'"
    )

    dump = backup()

    admin_dsn = _with_database(settings.database_url, "postgres")
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        await admin.execute(f'CREATE DATABASE "{scratch}" OWNER "{user}"')
    finally:
        await admin.close()

    print(f"pg_restore into {scratch}", flush=True)
    restored_dsn = _with_database(settings.database_url, scratch)
    import os
    import shutil

    if shutil.which("pg_restore"):
        done = _pg("pg_restore", restored_dsn, "--no-owner", "--no-privileges", str(dump))
    else:
        _h, _p, ruser, rpassword, rdatabase = _dsn_parts(restored_dsn)
        container = os.environ.get("PGDUMP_CONTAINER", "clinicalcontext-postgres")
        with dump.open("rb") as handle:
            raw = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "-e",
                    f"PGPASSWORD={rpassword}",
                    container,
                    "pg_restore",
                    "-h",
                    "localhost",
                    "-p",
                    "5432",
                    "-U",
                    ruser,
                    "-d",
                    rdatabase,
                    "--no-owner",
                    "--no-privileges",
                ],
                stdin=handle,
                capture_output=True,
                check=False,
            )
        done = subprocess.CompletedProcess(
            raw.args, raw.returncode, raw.stdout.decode(), raw.stderr.decode()
        )
    # pg_restore reports non-fatal warnings (extension ownership, roles) with
    # a non-zero code; the comparison below is the real verdict.
    if done.returncode != 0:
        tail = done.stderr.strip().splitlines()
        print(f"  pg_restore warnings: {tail[-1][:160] if tail else ''}")

    copy = await asyncpg.connect(restored_dsn)
    ok = True
    try:
        after = {
            table: await live.fetchval(f"SELECT count(*) FROM public.{table}") for table in COMPARED
        }
        print()
        print(f"{'table':20s} {'at dump':>9s} {'restored':>9s} {'now':>9s}")
        for table in COMPARED:
            restored = await copy.fetchval(f"SELECT count(*) FROM public.{table}")
            consistent = before[table] <= restored <= after[table]
            ok = ok and consistent
            mark = "" if consistent else "  <- MISMATCH"
            print(f"{table:20s} {before[table]:9d} {restored:9d} {after[table]:9d}{mark}")

        policies_copy = await copy.fetchval(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public'"
        )
        print(f"{'RLS policies':20s} {policies_before:9d} {policies_copy:9d}")
        ok = ok and policies_before == policies_copy
        # A restored database that lost its RLS is a restored database that
        # leaks, so this is checked explicitly rather than assumed.
        unprotected = await copy.fetch(
            """
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
            """
        )
        if unprotected:
            ok = False
            print(f"  tables without RLS after restore: {[r['relname'] for r in unprotected]}")
        # And the corpus is genuinely readable, not just counted.
        sample = await copy.fetchval(
            "SELECT title FROM public.documents WHERE org_id IS NULL ORDER BY ingested_at LIMIT 1"
        )
        print(f"  first document reads back: {str(sample)[:60]!r}")
        ok = ok and bool(sample)
    finally:
        await live.close()
        await copy.close()

    if not keep:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        finally:
            await admin.close()
        print()
        print(f"dropped {scratch}")

    print()
    print("restore drill:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("analyze", help="VACUUM ANALYZE and report index health")
    backup_parser = sub.add_parser("backup", help="pg_dump the database")
    backup_parser.add_argument("--out", type=Path)
    drill = sub.add_parser("restore-drill", help="dump, restore into a scratch database, compare")
    drill.add_argument("--keep", action="store_true", help="keep the restored database")
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return asyncio.run(analyze())
    if args.command == "backup":
        backup(args.out)
        return 0
    return asyncio.run(restore_drill(keep=args.keep))


if __name__ == "__main__":
    sys.exit(main())
