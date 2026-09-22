#!/usr/bin/env python3
"""Run everything CI runs, here, before pushing.

CI stayed red for four commits over two whitespace characters, and a fifth
over a stale OpenAPI schema — each time because the tools were run piecemeal
rather than as the set CI actually gates on. This runs that set, in CI's
order, and prints one table.

    python scripts/preflight.py            # everything
    python scripts/preflight.py --fast     # skip the slow suites
    python scripts/preflight.py --backend  # one side only

The frontend build gets CI's placeholder origins because lib/csp.ts refuses
to build a production bundle whose Content-Security-Policy points at
localhost; a developer's .env.local usually does.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# Mirrors .github/workflows/ci.yml, "Build (next build)".
BUILD_ENV = {
    "NEXT_PUBLIC_API_URL": "https://api.ci-build.invalid",
    "NEXT_PUBLIC_SUPABASE_URL": "https://ci-build.supabase.co",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "ci-build-placeholder",
    "NEXT_PUBLIC_OTEL_EXPORTER_URL": "",
    # Keep the verification build out of .next, which a running `next dev`
    # holds open — building over it leaves the dev server serving 500s.
    "NEXT_DIST_DIR": ".next-preflight",
}


@dataclass(slots=True)
class Step:
    name: str
    argv: list[str]
    cwd: Path
    slow: bool = False
    env: dict[str, str] | None = None


def python_for_backend() -> list[str]:
    """The backend's own interpreter, so this works without `uv` on PATH."""
    venv = BACKEND / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    exe = venv.with_suffix(".exe") if os.name == "nt" else venv
    if exe.exists():
        return [str(exe), "-m"]
    if shutil.which("uv"):
        return ["uv", "run", "python", "-m"]
    return [sys.executable, "-m"]


def pnpm() -> str:
    return shutil.which("pnpm") or "pnpm"


def backend_steps(fast: bool) -> list[Step]:
    py = python_for_backend()
    steps = [
        Step("ruff check", [*py, "ruff", "check", "."], BACKEND),
        Step("ruff format --check", [*py, "ruff", "format", "--check", "."], BACKEND),
        Step("mypy --strict", [*py, "mypy", "--strict", "app", "scripts", "evals"], BACKEND),
        Step(
            "mypy --strict (win32)",
            [*py, "mypy", "--strict", "--platform", "win32", "app", "scripts", "evals"],
            BACKEND,
        ),
        Step("openapi is current", [*py, "scripts.export_openapi", "--check"], BACKEND),
    ]
    if not fast:
        steps.append(Step("pytest", [*py, "pytest", "-q"], BACKEND, slow=True))
    return steps


def frontend_steps(fast: bool) -> list[Step]:
    steps = [
        Step("eslint", [pnpm(), "lint"], FRONTEND),
        Step("tsc --noEmit", [pnpm(), "typecheck"], FRONTEND),
    ]
    if not fast:
        steps.append(Step("next build", [pnpm(), "build"], FRONTEND, slow=True, env=BUILD_ENV))
    return steps


def run(step: Step) -> tuple[bool, float, str]:
    env = {**os.environ, **(step.env or {})}
    started = time.monotonic()
    proc = subprocess.run(
        step.argv, cwd=step.cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    elapsed = time.monotonic() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, elapsed, output.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="skip pytest and the production build")
    parser.add_argument("--backend", action="store_true", help="backend steps only")
    parser.add_argument("--frontend", action="store_true", help="frontend steps only")
    args = parser.parse_args()

    steps: list[Step] = []
    if not args.frontend:
        steps += backend_steps(args.fast)
    if not args.backend:
        steps += frontend_steps(args.fast)

    results: list[tuple[Step, bool, float, str]] = []
    for step in steps:
        marker = " (slow)" if step.slow else ""
        print(f"... {step.name}{marker}", flush=True)
        ok, elapsed, output = run(step)
        results.append((step, ok, elapsed, output))
        if not ok:
            print(output[-4000:], file=sys.stderr)

    print("\n" + "=" * 60)
    failures = 0
    for step, ok, elapsed, _ in results:
        status = "PASS" if ok else "FAIL"
        failures += 0 if ok else 1
        print(f"{status:5} {step.name:24} {elapsed:6.1f}s")
    print("=" * 60)
    if failures:
        print(f"{failures} step(s) failed — CI would fail too.")
        return 1
    scope = "fast subset" if args.fast else "everything CI gates on"
    print(f"All green ({scope}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
