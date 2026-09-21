"""Export the OpenAPI schema to a file.

The schema is generated from the live app, so it is whatever FastAPI will
actually serve — it cannot drift from the routes the way a hand-maintained
spec does. The frontend's TypeScript types are generated from this file
(`pnpm gen:api`), which makes a breaking API change show up as a TypeScript
error rather than a runtime surprise.

    uv run python -m scripts.export_openapi              # -> docs/openapi.json
    uv run python -m scripts.export_openapi --check      # CI: fail if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def build_schema() -> dict[str, object]:
    from app.main import create_app

    schema: dict[str, object] = create_app().openapi()
    return schema


def render(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the file on disk is missing or out of date",
    )
    args = parser.parse_args(argv)

    schema = build_schema()
    rendered = render(schema)
    output: Path = args.output

    if args.check:
        if not output.exists():
            print(f"{output} does not exist; run scripts.export_openapi", file=sys.stderr)
            return 1
        if output.read_text(encoding="utf-8") != rendered:
            print(
                f"{output} is out of date; run `uv run python -m scripts.export_openapi`",
                file=sys.stderr,
            )
            return 1
        print(f"{output} is up to date")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    paths = schema.get("paths")
    count = len(paths) if isinstance(paths, dict) else 0
    print(f"wrote {output} ({count} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
