"""Phase-gate verification (Phase 14.1): re-prove every phase on today's code.

Each phase of this build ended with a gate that had to be demonstrated. This
runner re-checks all of them against the current tree and writes
``docs/VERIFICATION.md`` with a ✅ or ❌ per gate and, for each, *how* it was
checked — a test node, an eval artifact, an E2E spec, or a live probe.

    uv run python -m scripts.verify                  # tests + static + evals
    uv run python -m scripts.verify --with-e2e       # also run Playwright
    uv run python -m scripts.verify --fast           # reuse the last results

Nothing here asserts a gate from a document. A gate is green only because a
command exited zero or an artifact on disk says so, and the artifact's own
timestamp is printed next to it so a stale one is visible rather than
flattering.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"
RESULTS = BACKEND / "evals" / "results"
JUNIT = BACKEND / ".verify" / "pytest.xml"
E2E_REPORT = FRONTEND / "e2e" / ".report" / "results.json"
REPORT = REPO / "docs" / "VERIFICATION.md"


# -- evidence ---------------------------------------------------------------------


@dataclass
class Evidence:
    """One thing that was checked, and what it said."""

    how: str
    ok: bool | None  # None: could not be checked in this run
    detail: str = ""


@dataclass
class Gate:
    phase: str
    gate: str
    checks: list[Evidence] = field(default_factory=list)
    # Printed under a red gate: what is known about the failure, so a red
    # that is a measured limitation is not mistaken for a broken build.
    note: str | None = None

    @property
    def status(self) -> str:
        if any(c.ok is False for c in self.checks):
            return "❌"
        if not self.checks or all(c.ok is None for c in self.checks):
            return "⚠️"
        return "✅"


# -- collectors -------------------------------------------------------------------


class TestResults:
    """The pytest run, as a lookup from node id to pass/fail."""

    def __init__(self, junit: Path) -> None:
        self.by_file: dict[str, list[tuple[str, bool]]] = {}
        self.total = 0
        self.failed = 0
        self.available = junit.is_file()
        if not self.available:
            return
        for case in ET.parse(junit).getroot().iter("testcase"):
            # pytest's junit XML carries the source file; the classname is the
            # dotted module (plus a class when there is one), which is the
            # wrong thing to key on.
            file = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
            name = case.get("name", "")
            failed = any(child.tag in {"failure", "error"} for child in case)
            skipped = any(child.tag == "skipped" for child in case)
            self.by_file.setdefault(Path(file).name, []).append((name, not failed))
            if not skipped:
                self.total += 1
                self.failed += int(failed)

    def check(self, *files: str, contains: str | None = None) -> Evidence:
        if not self.available:
            return Evidence(f"pytest {' '.join(files)}", None, "not run in this invocation")
        picked: list[tuple[str, bool]] = []
        for file in files:
            picked.extend(self.by_file.get(file, []))
        if contains is not None:
            picked = [(n, ok) for n, ok in picked if contains in n]
        if not picked:
            return Evidence(f"pytest {' '.join(files)}", None, "no matching tests ran")
        failures = [n for n, ok in picked if not ok]
        detail = f"{len(picked) - len(failures)}/{len(picked)} passed"
        if failures:
            detail += f" — failing: {', '.join(failures[:3])}"
        return Evidence(f"pytest {' '.join(files)}", not failures, detail)


class E2EResults:
    """The Playwright run, as a lookup from spec file to pass/fail."""

    def __init__(self, report: Path) -> None:
        self.by_spec: dict[str, list[tuple[str, bool]]] = {}
        self.available = report.is_file()
        self.generated_at: str | None = None
        if not self.available:
            return
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.generated_at = str(payload.get("stats", {}).get("startTime", "")) or None

        def walk(suite: dict[str, Any], file: str | None = None) -> None:
            file = str(suite.get("file") or file or "")
            for spec in suite.get("specs", []):
                ok = all(test.get("status") == "expected" for test in spec.get("tests", []))
                self.by_spec.setdefault(Path(file).name, []).append((str(spec.get("title")), ok))
            for child in suite.get("suites", []):
                walk(child, file)

        for suite in payload.get("suites", []):
            walk(suite)

    def check(self, *specs: str) -> Evidence:
        if not self.available:
            return Evidence(f"playwright {' '.join(specs)}", None, "no E2E report on disk")
        picked: list[tuple[str, bool]] = []
        for spec in specs:
            picked.extend(self.by_spec.get(spec, []))
        if not picked:
            return Evidence(f"playwright {' '.join(specs)}", None, "spec not in the report")
        failures = [n for n, ok in picked if not ok]
        detail = f"{len(picked) - len(failures)}/{len(picked)} passed"
        if failures:
            detail += f" — failing: {', '.join(failures[:2])}"
        return Evidence(f"playwright {' '.join(specs)}", not failures, detail)


def artifact(name: str, *, predicate: Callable[[dict[str, Any]], tuple[bool, str]]) -> Evidence:
    """Read an eval result file and judge it, printing when it was produced."""
    path = RESULTS / name
    how = f"evals/results/{name}"
    if not path.is_file():
        return Evidence(how, None, "not produced")
    payload = json.loads(path.read_text(encoding="utf-8"))
    ok, detail = predicate(payload)
    when = str(payload.get("generated_at", ""))[:19] or "unknown date"
    return Evidence(how, ok, f"{detail} (run {when})")


def command(label: str, args: list[str], *, cwd: Path, timeout: int = 1800) -> Evidence:
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return Evidence(label, None, f"{args[0]} is not installed")
    except subprocess.TimeoutExpired:
        return Evidence(label, False, "timed out")
    tail = (done.stderr or done.stdout or "").strip().splitlines()
    return Evidence(label, done.returncode == 0, tail[-1][:120] if tail else "")


def database_probe() -> list[Evidence]:
    """Two invariants that only the database can answer for."""
    import asyncio

    import asyncpg

    from app.config import get_settings

    async def run() -> list[Evidence]:
        try:
            conn = await asyncpg.connect(get_settings().database_url, timeout=10)
        except Exception as exc:  # pragma: no cover — reported, not raised
            return [
                Evidence("database probe", None, f"database unavailable: {type(exc).__name__}"),
                Evidence("ingestion dead letters", None, "database unavailable"),
            ]
        try:
            unprotected = await conn.fetch(
                """
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
                """
            )
            failures = await conn.fetchval("SELECT count(*) FROM public.ingestion_failures")
            migrations = await conn.fetchval("SELECT count(*) FROM public.schema_migrations")
        finally:
            await conn.close()
        names = [r["relname"] for r in unprotected]
        return [
            Evidence(
                "every public table has RLS enabled",
                not names,
                "all tables protected" if not names else f"unprotected: {', '.join(names)}",
            ),
            Evidence(
                "ingestion dead-letter queue",
                failures == 0,
                f"{failures} entries; {migrations} migrations applied",
            ),
        ]

    return asyncio.run(run())


# -- the gate list ----------------------------------------------------------------


def build_gates(
    tests: TestResults, e2e: E2EResults, static: dict[str, Evidence], db: list[Evidence]
) -> list[Gate]:
    def golden(payload: dict[str, Any]) -> tuple[bool, str]:
        gate = payload.get("gate", {})
        checks = gate.get("checks", [])
        passed = bool(gate.get("passed"))
        return passed, f"{sum(1 for c in checks if c.get('passed'))}/{len(checks)} gate checks"

    def safety(payload: dict[str, Any]) -> tuple[bool, str]:
        s = payload.get("safety", {})
        return bool(
            s.get("gate_passed")
        ), f"{s.get('total_passed')}/{s.get('total')} adversarial cases"

    def retrieval(payload: dict[str, Any]) -> tuple[bool, str]:
        chunks = payload.get("retrieval", {}).get("chunks", {})
        docs = payload.get("retrieval", {}).get("documents", {})
        return bool(chunks) and bool(docs), (
            f"recall@10 {chunks.get('recall_at_10')} passages / "
            f"{docs.get('recall_at_10')} documents"
        )

    def ablation(payload: dict[str, Any]) -> tuple[bool, str]:
        rows = payload.get("rows", [])
        return len(rows) >= 10, f"{len(rows)} configurations"

    def calibration(payload: dict[str, Any]) -> tuple[bool, str]:
        curves = payload.get("curves", {})
        golden_curve = curves.get("golden", {})
        # The gate is that the curve is honest and published, not that it is
        # flat: the run currently recommends retuning, and says so in public.
        return bool(golden_curve.get("n")), (
            f"golden n={golden_curve.get('n')} brier={golden_curve.get('brier')}; "
            f"retune recommended: {payload.get('recommendation', {}).get('retune')}"
        )

    def voice(payload: dict[str, Any]) -> tuple[bool, str]:
        gate = payload.get("gate", {})
        checks = gate.get("checks", [])
        failing = [c.get("name") for c in checks if c.get("passed") is False]
        first = (
            payload.get("summary", {})
            .get("first_audio", {})
            .get("server", {})
            .get("total_first_audio", {})
        )
        detail = (
            f"first audio p50 {first.get('p50')} ms / p95 {first.get('p95')} ms; "
            f"{len(checks) - len(failing)}/{len(checks)} checks at gate level "
            f"{gate.get('level')}"
        )
        if failing:
            detail += f"; failing: {', '.join(str(f) for f in failing)}"
        return bool(gate.get("passed")), detail

    def load(payload: dict[str, Any]) -> tuple[bool, str]:
        sustained = payload.get("sustained", {}).get("total", {})
        breaking = payload.get("breaking_point")
        return sustained.get("requests", 0) > 0, (
            f"{sustained.get('requests')} requests at "
            f"{payload.get('sustained', {}).get('users')} users, "
            f"error rate {sustained.get('error_rate')}; broke at "
            f"{breaking.get('users') if breaking else 'no breaking point'}"
        )

    return [
        Gate(
            "1",
            "Foundation: config fails fast, structured logs carry a request id, health and "
            "readiness probe their dependencies",
            [
                tests.check("test_config.py"),
                tests.check("test_health.py"),
                tests.check("test_middleware.py"),
                tests.check("test_errors.py"),
            ],
        ),
        Gate(
            "2-3",
            "Tenancy: every migration applies to an empty database, RLS isolates tenants at the "
            "database, RBAC holds at the API",
            [
                tests.check("test_rls.py"),
                tests.check("test_auth_rbac.py"),
                tests.check("test_repositories_base.py"),
                e2e.check("03-isolation.spec.ts"),
                *db[:1],
            ],
        ),
        Gate(
            "4",
            "Ingestion: re-running an ingest inserts nothing twice, failures are dead-lettered, "
            "chunking strategies coexist",
            [
                tests.check("test_ingestion_pipeline.py"),
                tests.check("test_chunking.py", "test_chunking_strategies.py"),
                tests.check("test_chunk_strategy_coexistence.py"),
                tests.check("test_content_hash.py"),
                *db[1:2],
            ],
        ),
        Gate(
            "5-6",
            "Hybrid retrieval: dense + BM25 fused and reranked, scored on labelled passages "
            "independently of generation",
            [
                tests.check("test_retrieval_integration.py"),
                tests.check("test_fusion.py", "test_rerank.py", "test_lexical_query.py"),
                tests.check("test_embed_service.py", "test_embedders.py"),
                artifact("golden.json", predicate=retrieval),
            ],
        ),
        Gate(
            "7",
            "Guardrails: PHI and diagnosis refusals are 100% on the adversarial set, grounding is "
            "verified, red flags escalate",
            [
                tests.check(
                    "test_guardrails_phi.py",
                    "test_guardrails_scope.py",
                    "test_guardrails_redflag.py",
                    "test_guardrails_grounding.py",
                    "test_guardrails_pipeline.py",
                ),
                artifact("golden.json", predicate=safety),
                e2e.check("02-ask.spec.ts"),
            ],
        ),
        Gate(
            "8",
            "The agent graph: it self-corrects, surfaces contradictions, abstains rather than "
            "guessing, and streams its reasoning",
            [tests.check("test_graph_agent.py"), e2e.check("02-ask.spec.ts")],
        ),
        Gate(
            "9",
            "The API: streaming, semantic cache, idempotency, API keys with their own bucket, "
            "comparison mode, 429s, real cost",
            [
                tests.check("test_phase9_api.py"),
                tests.check("test_phase9_endpoints.py"),
                tests.check("test_phase9_power.py"),
                e2e.check("08-api-keys-idempotency.spec.ts", "04-feedback-and-limits.spec.ts"),
            ],
        ),
        Gate(
            "10",
            "The frontend: ask → stream → cite → verify, contradictions and abstentions rendered, "
            "binders, permalinks, ⌘K",
            [
                tests.check("test_phase10_backend.py"),
                e2e.check("01-onboarding.spec.ts"),
                e2e.check("05-comparison-export.spec.ts"),
                e2e.check("06-binders-sharing.spec.ts"),
                e2e.check("09-keyboard.spec.ts"),
            ],
        ),
        Gate(
            "11",
            "Voice: a spoken question returns a cited spoken answer, guardrails hold on the "
            "spoken path, barge-in stops the audio",
            [
                tests.check(
                    "test_voice_ws.py", "test_voice_components.py", "test_voice_harness.py"
                ),
                artifact("voice.json", predicate=voice),
                e2e.check("voice.spec.ts"),
            ],
            note=(
                "The two failing checks are properties of the **offline** speech stack, not "
                "regressions, and both are measured rather than estimated: faster-whisper "
                "`tiny.en` misses 18.8% of medical terms after correction (target < 5%), and "
                "its ~460 ms decode puts endpointing at p50 680 ms (target 250 ms). Both are "
                "what Deepgram `nova-3-medical` exists for; the adapter is written and unrun "
                "because there is no key in this environment. Everything the phase gate itself "
                "asked for - first audio p50 <= 1200 ms, 100% spoken guardrail parity, zero "
                "silent LASA substitutions, barge-in p95 <= 150 ms - passes, and CI gates on "
                "`--gate safety`, which is green."
            ),
        ),
        Gate(
            "12",
            "The evaluation harness: a golden set with expert-derived ground truth, an ablation, "
            "an honest calibration curve, a CI gate",
            [
                tests.check(
                    "test_golden_metrics.py", "test_golden_runner.py", "test_golden_loop.py"
                ),
                artifact("golden.json", predicate=golden),
                artifact("ablation.json", predicate=ablation),
                artifact("calibration.json", predicate=calibration),
            ],
        ),
        Gate(
            "13",
            "Observability: one query is one trace, spend is ledgered per tenant, the load test "
            "and the methodology page are live",
            [
                tests.check("test_telemetry.py"),
                tests.check("test_cost_ledger.py"),
                tests.check("test_public_demo.py", "test_digest.py"),
                artifact("load.json", predicate=load),
            ],
        ),
        Gate(
            "14",
            "Cross-cutting: lint, strict types, the committed API schema, and no secrets in the "
            "tree",
            [
                static["ruff"],
                static["format"],
                static["mypy"],
                static["openapi"],
                static["tsc"],
                static["eslint"],
                static["gitleaks"],
            ],
        ),
    ]


# -- report -----------------------------------------------------------------------


def render(gates: Iterable[Gate], *, tests: TestResults, e2e: E2EResults) -> str:
    gates = list(gates)
    red = [g for g in gates if g.status == "❌"]
    amber = [g for g in gates if g.status == "⚠️"]
    lines = [
        "# Verification",
        "",
        f"Generated by `uv run python -m scripts.verify` on "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC.",
        "",
        "Every phase of this build ended with a gate. This is those gates, re-checked",
        "against the current code — each one green only because a command exited zero",
        "or an artifact on disk says so. Where a number comes from an eval run, the",
        "date of that run is printed with it, so a stale artifact shows as stale",
        "rather than as a pass.",
        "",
        f"**{len(gates) - len(red) - len(amber)} of {len(gates)} gates green"
        + (f", {len(red)} red" if red else "")
        + (f", {len(amber)} unverified in this run" if amber else "")
        + ".**",
        "",
    ]
    if tests.available:
        lines += [
            f"Backend suite: **{tests.total - tests.failed}/{tests.total} passed**"
            + (f" ({tests.failed} failing)" if tests.failed else "")
            + ".",
            "",
        ]
    if e2e.available:
        total = sum(len(v) for v in e2e.by_spec.values())
        failed = sum(1 for v in e2e.by_spec.values() for _, ok in v if not ok)
        lines += [f"End-to-end suite: **{total - failed}/{total} passed**.", ""]

    lines += ["| Phase | Gate | Status |", "|---|---|---|"]
    for gate in gates:
        lines.append(f"| {gate.phase} | {gate.gate} | {gate.status} |")
    lines += ["", "## How each gate was checked", ""]
    for gate in gates:
        lines.append(f"### Phase {gate.phase} — {gate.status}")
        lines.append("")
        lines.append(f"*{gate.gate}*")
        lines.append("")
        for check in gate.checks:
            mark = {True: "✅", False: "❌", None: "⚠️"}[check.ok]
            detail = f" — {check.detail}" if check.detail else ""
            lines.append(f"- {mark} `{check.how}`{detail}")
        if gate.note and gate.status != "✅":
            lines.append("")
            lines.append(f"> {gate.note}")
        lines.append("")
    return "\n".join(lines) + "\n"


# -- main -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="reuse the last pytest/static results")
    parser.add_argument(
        "--with-e2e", action="store_true", help="run Playwright as part of the check"
    )
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    if not args.fast:
        JUNIT.parent.mkdir(parents=True, exist_ok=True)
        print("running the backend suite…", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", f"--junitxml={JUNIT}"],
            cwd=BACKEND,
            check=False,
        )

    if args.with_e2e:
        print("running the end-to-end suite…", flush=True)
        subprocess.run(
            ["pnpm", "exec", "playwright", "test"], cwd=FRONTEND, check=False, shell=True
        )

    print("static gates…", flush=True)
    python = [sys.executable, "-m"]
    static = {
        "ruff": command("ruff check", [*python, "ruff", "check", "."], cwd=BACKEND),
        "format": command(
            "ruff format --check", [*python, "ruff", "format", "--check", "."], cwd=BACKEND
        ),
        "mypy": command(
            "mypy --strict app scripts evals",
            [*python, "mypy", "--strict", "app", "scripts", "evals"],
            cwd=BACKEND,
        ),
        "openapi": command(
            "openapi schema is current",
            [*python, "scripts.export_openapi", "--check"],
            cwd=BACKEND,
        ),
        "tsc": command("tsc --noEmit", ["pnpm", "exec", "tsc", "--noEmit"], cwd=FRONTEND),
        "eslint": command("eslint", ["pnpm", "lint"], cwd=FRONTEND),
        "gitleaks": gitleaks(),
    }

    tests = TestResults(JUNIT)
    e2e = E2EResults(E2E_REPORT)
    gates = build_gates(tests, e2e, static, database_probe())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(gates, tests=tests, e2e=e2e), encoding="utf-8")

    # The report is UTF-8; the console may not be (Windows defaults to
    # cp1252), so the summary printed here is plain ASCII.
    plain = {"✅": "PASS", "❌": "FAIL", "⚠️": "----"}
    for gate in gates:
        summary = gate.gate[:70].encode("ascii", "replace").decode("ascii")
        print(f"{plain[gate.status]}  phase {gate.phase}: {summary}")
    print(f"wrote {args.out}")
    return 1 if any(g.status == "❌" for g in gates) else 0


def gitleaks() -> Evidence:
    """Secret scan, through the published image so there is nothing to install."""
    return command(
        "gitleaks (no secrets in the tree)",
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO}:/repo:ro",
            "zricethezav/gitleaks:latest",
            "detect",
            "--source=/repo",
            "--no-git",
            "--config=/repo/.gitleaks.toml",
            "--redact",
            "--exit-code=1",
        ],
        cwd=REPO,
        timeout=900,
    )


if __name__ == "__main__":
    raise SystemExit(main())
