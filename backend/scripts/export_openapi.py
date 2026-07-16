from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "openapi.json"
sys.path.insert(0, str(ROOT))

from app.bootstrap import create_app  # noqa: E402
from app.core.config import Environment, Settings  # noqa: E402


def normalized_schema() -> str:
    app = create_app(settings=Settings(environment=Environment.TEST, log_level="CRITICAL"))
    schema: dict[str, Any] = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the deterministic OpenAPI schema")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    generated = normalized_schema()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != generated:
            print(f"OpenAPI schema is stale: {args.output}")
            return 1
        print(f"OpenAPI schema is current: {args.output}")
        return 0

    args.output.write_text(generated, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
