"""Load test (Phase 13): 50 concurrent clinicians, then a ramp to the breaking
point — against a running API.

    python -m evals.load.run --base-url http://127.0.0.1:8010
    python -m evals.load.run --users 50 --duration 60 --ramp 25,50,100,150,200

Two measurements, in one run, against one target:

1. **Sustained** — ``--users`` simulated clinicians for ``--duration``
   seconds, after the spawn completes. This is the headline: p50/p95/p99 to
   the answer, the error rate, and how much of the traffic the semantic cache
   carried.
2. **Ramp** — user counts from ``--ramp``, each held for ``--stage-seconds``,
   stopping at the first stage where the error rate passes
   ``--max-error-rate`` or p95 passes ``--p95-limit-ms``. That stage is the
   breaking point, and the last stage that passed is the capacity the
   methodology page reports.

Every number is what Locust measured against the target; the machine and
backend that produced them are recorded next to them, because a laptop on
the offline backend and a cloud deployment on Anthropic + Cohere are
different systems with different numbers.
"""

from __future__ import annotations

# isort: off
# Locust must be imported before anything that imports ``ssl``: it
# monkey-patches the standard library for gevent, and an ``ssl`` module that
# was imported first breaks every TLS context created afterwards.
import locust  # noqa: F401

# isort: on
import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[1] / "results" / "load.json"
ASK_NAMES = ("ask (pipeline)", "ask (cache)", "ask (blocked)", "ask (failed)")


# -- tenant lifecycle (separate processes: see seed.py) ----------------------------------


def seed_tenant() -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, "-m", "evals.load.seed"], check=True, capture_output=True, text=True
    )
    tenant: dict[str, str] = json.loads(out.stdout)
    return tenant


def remove_tenant(tenant: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "evals.load.seed", "--cleanup", tenant["org_id"], tenant["user_id"]],
        check=True,
    )


# -- the stages ------------------------------------------------------------------------


def _entry_summary(entry: Any) -> dict[str, Any]:
    requests = int(entry.num_requests)
    failures = int(entry.num_failures)
    return {
        "requests": requests,
        "failures": failures,
        "error_rate": round(failures / requests, 4) if requests else None,
        "p50_ms": _pct(entry, 0.5),
        "p95_ms": _pct(entry, 0.95),
        "p99_ms": _pct(entry, 0.99),
        "avg_ms": round(float(entry.avg_response_time), 1) if requests else None,
        "max_ms": round(float(entry.max_response_time), 1) if requests else None,
        "rps": round(float(entry.total_rps), 3) if requests else 0.0,
    }


def _pct(entry: Any, p: float) -> float | None:
    if not entry.num_requests:
        return None
    value = entry.get_response_time_percentile(p)
    return round(float(value), 1) if value is not None else None


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None}
    ordered = sorted(values)

    def at(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 1)

    return {"p50_ms": at(0.5), "p95_ms": at(0.95), "p99_ms": at(0.99)}


def snapshot(env: Any, *, users: int, seconds: float) -> dict[str, Any]:
    """Everything Locust measured in the stage, per endpoint and overall."""
    from evals.load.locustfile import FIRST_EVENT_MS

    first_event = {"count": len(FIRST_EVENT_MS), **_percentiles(FIRST_EVENT_MS)}
    FIRST_EVENT_MS.clear()
    by_name: dict[str, dict[str, Any]] = {}
    for (name, method), entry in env.stats.entries.items():
        if entry.num_requests:
            by_name[name] = {"method": method, **_entry_summary(entry)}
    asked = sum(by_name.get(n, {}).get("requests", 0) for n in ASK_NAMES)
    cached = by_name.get("ask (cache)", {}).get("requests", 0)
    failures = [
        {"name": name, "error": str(error), "count": int(count)}
        for (name, error), count in _failure_counts(env).items()
    ]
    return {
        "users": users,
        "seconds": round(seconds, 1),
        "total": _entry_summary(env.stats.total),
        "asks": asked,
        "cache_hit_rate": round(cached / asked, 4) if asked else None,
        "first_event": first_event,
        "by_endpoint": by_name,
        "failures": failures,
    }


def _failure_counts(env: Any) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for error in env.stats.errors.values():
        counts[(error.name, error.error)] = counts.get((error.name, error.error), 0) + int(
            error.occurrences
        )
    return counts


def run_stage(
    env: Any, runner: Any, *, users: int, spawn_rate: float, seconds: float
) -> dict[str, Any]:
    """Spawn to ``users``, then measure a clean window of ``seconds``."""
    import gevent  # type: ignore[import-untyped]

    runner.start(users, spawn_rate=spawn_rate)
    runner.spawning_greenlet.join()
    env.stats.reset_all()
    from evals.load.locustfile import FIRST_EVENT_MS

    FIRST_EVENT_MS.clear()
    started = time.perf_counter()
    gevent.sleep(seconds)
    return snapshot(env, users=users, seconds=time.perf_counter() - started)


def breaking(stage: dict[str, Any], *, max_error_rate: float, p95_limit_ms: float) -> str | None:
    total = stage["total"]
    if total["requests"] == 0:
        return "no request completed in the stage"
    if total["error_rate"] is not None and total["error_rate"] > max_error_rate:
        return f"error rate {total['error_rate']:.1%} > {max_error_rate:.0%}"
    asks = stage["by_endpoint"].get("ask (pipeline)") or stage["by_endpoint"].get("ask (cache)")
    if asks and asks["p95_ms"] is not None and asks["p95_ms"] > p95_limit_ms:
        return f"ask p95 {asks['p95_ms']:.0f} ms > {p95_limit_ms:.0f} ms"
    return None


# -- the run -----------------------------------------------------------------------------


def machine_description() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def check_target(base_url: str) -> None:
    import requests

    response = requests.get(f"{base_url}/health", timeout=5)
    response.raise_for_status()


def run(args: argparse.Namespace) -> dict[str, Any]:
    # Before Locust is imported (it monkey-patches for gevent): describe the
    # backend with the app's own code and seed the tenant in a subprocess.
    from evals.golden.run import backend_description

    backend = backend_description()
    check_target(args.base_url)
    tenant = seed_tenant()
    os.environ["CC_LOAD_API_KEY"] = tenant["api_key"]
    try:
        return _run_locust(args, backend=backend)
    finally:
        os.environ.pop("CC_LOAD_API_KEY", None)
        if not args.keep_tenant:
            remove_tenant(tenant)


def _run_locust(args: argparse.Namespace, *, backend: dict[str, Any]) -> dict[str, Any]:
    from locust.env import Environment

    from evals.load.locustfile import QUESTIONS, ClinicalUser

    env = Environment(user_classes=[ClinicalUser], host=args.base_url)
    runner = env.create_local_runner()
    started_at = datetime.now(UTC)
    print(f"target {args.base_url} · {len(QUESTIONS)} questions · backend {backend['reasoner']}")

    print(f"sustained: {args.users} users for {args.duration}s")
    sustained = run_stage(
        env, runner, users=args.users, spawn_rate=args.spawn_rate, seconds=args.duration
    )
    _print_stage("sustained", sustained)

    ramp: list[dict[str, Any]] = []
    breaking_point: dict[str, Any] | None = None
    if args.ramp:
        for users in args.ramp:
            print(f"ramp: {users} users for {args.stage_seconds}s")
            stage = run_stage(
                env, runner, users=users, spawn_rate=args.spawn_rate, seconds=args.stage_seconds
            )
            _print_stage(f"ramp {users}", stage)
            reason = breaking(
                stage, max_error_rate=args.max_error_rate, p95_limit_ms=args.p95_limit_ms
            )
            stage["broke"] = reason
            ramp.append(stage)
            if reason is not None:
                breaking_point = {"users": users, "reason": reason}
                print(f"breaking point at {users} users: {reason}")
                break
    runner.quit()

    passed = [s for s in ramp if not s["broke"]]
    return {
        "generated_at": started_at.isoformat(),
        "target": args.base_url,
        "backend": backend,
        "machine": machine_description(),
        "questions": len(QUESTIONS),
        "criteria": {"max_error_rate": args.max_error_rate, "p95_limit_ms": args.p95_limit_ms},
        "sustained": sustained,
        "ramp": ramp,
        "breaking_point": breaking_point,
        "capacity_users": passed[-1]["users"] if passed else None,
        "notes": [
            "Latency is time to the SSE `result` event (the answer), per simulated user; "
            "`first_event` is time to the first stream event of successful asks.",
            "Cache hits are recorded separately from pipeline answers; the same question "
            "asked again by any user of the tenant is a semantic-cache hit.",
            "The tenant is on the enterprise plan (API-key limit 3000/min); any 429 is "
            "recorded as a failure.",
        ],
    }


def _print_stage(label: str, stage: dict[str, Any]) -> None:
    total = stage["total"]
    ask = stage["by_endpoint"].get("ask (pipeline)", {})
    print(
        f"  {label}: {total['requests']} req · errors {total['error_rate']} · "
        f"p50 {total['p50_ms']} · p95 {total['p95_ms']} · p99 {total['p99_ms']} ms · "
        f"{total['rps']} rps · pipeline p95 {ask.get('p95_ms')} ms · "
        f"cache hit rate {stage['cache_hit_rate']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--duration", type=float, default=60.0, help="sustained stage seconds")
    parser.add_argument("--spawn-rate", type=float, default=10.0)
    parser.add_argument(
        "--ramp",
        type=lambda s: [int(x) for x in s.split(",") if x.strip()],
        default=[25, 50, 100, 150, 200, 300],
        help="user counts for the ramp; '' skips the ramp",
    )
    parser.add_argument("--stage-seconds", type=float, default=30.0)
    parser.add_argument("--max-error-rate", type=float, default=0.05)
    parser.add_argument("--p95-limit-ms", type=float, default=30_000.0)
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--keep-tenant", action="store_true")
    args = parser.parse_args()
    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
