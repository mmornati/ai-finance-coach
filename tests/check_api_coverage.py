"""The endpoint-coverage guard of test_api_zz_coverage.py over the CI shards: every endpoint of the OpenAPI schema must have had
at least one successful request in SOME shard. Each shard writes its calls with `pytest --shard K/N --api-calls-out FILE`.

Usage: python tests/check_api_coverage.py FILE...   (exit 1 when a shard is missing, no shard ran the guard, or an endpoint was missed)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from apihelpers import missing_endpoints


def check(files: list[Path]) -> list[str]:
    calls: set = set()
    spec: dict = {}
    shards: set[str] = set()
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        shards.add(data["shard"])
        calls.update(tuple(c) for c in data["calls"])
        spec.update(data["spec"])
    totals = {s.split("/")[1] for s in shards}
    if len(totals) != 1:
        return [f"the files come from different splits: {sorted(shards)}"]
    n = int(totals.pop())
    if shards != {f"{k}/{n}" for k in range(1, n + 1)}:
        return [f"shards missing: got {sorted(shards)} of {n}"]
    if not spec:
        return ["no shard ran test_api_zz_coverage.py: the schema is missing"]
    return [f"no successful call to {m}" for m in missing_endpoints(spec, calls)]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    problems = check([Path(a) for a in sys.argv[1:]])
    for p in problems:
        print(f"FAILED api coverage: {p}")
    if not problems:
        print(f"api coverage: every endpoint answered successfully across {len(sys.argv) - 1} shard(s)")
    sys.exit(1 if problems else 0)
